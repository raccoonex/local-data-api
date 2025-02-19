from __future__ import annotations

import json

from abc import ABC, abstractmethod
from base64 import b64encode
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

import jaydebeapi
from sqlalchemy import text
from sqlalchemy.engine import Dialect

from local_data_api.exceptions import BadRequestException
from local_data_api.models import ColumnMetadata, ExecuteStatementResponse, Field
from local_data_api.resources.resource import JDBCType, Resource

if TYPE_CHECKING:  # pragma: no cover
    from local_data_api.resources.resource import Connection, ConnectionMaker

# https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
"""

JDBC Data Type                                        | Data API Data Type
INTEGER, TINYINT, SMALLINT, BIGINT                    | LONG
FLOAT, REAL, DOUBLE                                   | DOUBLE
DECIMAL                                               | STRING
BOOLEAN, BIT                                          | BOOLEAN
BLOB, BINARY, LONGVARBINARY, VARBINARY                | BLOB
CLOB                                                  | STRING
Other types (including types related to date and time)| STRING
"""

LONG = [JDBCType.INTEGER, JDBCType.TINYINT, JDBCType.SMALLINT, JDBCType.BIGINT]
DOUBLE = [JDBCType.FLOAT, JDBCType.REAL, JDBCType.DOUBLE]
STRING = [JDBCType.DECIMAL, JDBCType.CLOB]
BOOLEAN = [JDBCType.BOOLEAN, JDBCType.BIT]
BLOB = [JDBCType.BLOB, JDBCType.BINARY, JDBCType.LONGVARBINARY, JDBCType.VARBINARY]
TIMESTAMP = [JDBCType.TIMESTAMP, JDBCType.TIMESTAMP_WITH_TIMEZONE]


def _fixed_to_datetime(rs: Any, col: Any) -> Optional[str]:  # pragma: no cover
    """
    jaydebeapi has a bug that can't be parsed datetime correctly.
    This workaround will be removed when the PR is merged.
    https://github.com/baztian/jaydebeapi/pull/177
    """
    java_val = rs.getTimestamp(col)
    if not java_val:
        return None
    import datetime

    d = datetime.datetime.strptime(str(java_val)[:19], "%Y-%m-%d %H:%M:%S")
    d = d.replace(microsecond=int(str(java_val.getNanos()).zfill(9)[:6]))
    return str(d)


jaydebeapi._DEFAULT_CONVERTERS['TIMESTAMP'] = _fixed_to_datetime


def attach_thread_to_jvm() -> None:
    "https://github.com/baztian/jaydebeapi/issues/14#issuecomment-261489331"

    import jpype

    if jpype.isJVMStarted() and not jpype.isThreadAttachedToJVM():
        jpype.attachThreadToJVM()
        jpype.java.lang.Thread.currentThread().setContextClassLoader(
            jpype.java.lang.ClassLoader.getSystemClassLoader()
        )


def connection_maker(
    jclassname: str,
    url: str,
    driver_args: Union[Dict, List] = None,
    jars: Union[List[str], str] = None,
    libs: Union[List[str], str] = None,
) -> ConnectionMaker:
    def connect(database: Optional[str] = None, **kwargs):  # type: ignore
        attach_thread_to_jvm()
        return jaydebeapi.connect(
            jclassname, url + database if database else url, driver_args, jars, libs
        )

    return connect


class JDBC(Resource, ABC):
    JDBC_NAME: str
    DRIVER: str
    DIALECT: Dialect

    def __init__(self, connection: Connection, transaction_id: Optional[str] = None):
        if transaction_id:
            attach_thread_to_jvm()
        super().__init__(connection, transaction_id)

    def get_field_from_value(self, value: Any) -> Field:
        return super().get_field_from_value(value)

    @abstractmethod
    def get_filed_from_jdbc_type(self, value: Any, jdbc_type: Optional[int]) -> Field:
        type_: Optional[JDBCType] = None
        if jdbc_type:
            try:
                type_ = JDBCType(jdbc_type)
            except ValueError:
                pass
        if type_:
            if value is None:
                return Field(isNull=True)
            elif type_ in LONG:
                return Field(longValue=value)
            elif type_ in DOUBLE:
                return Field(doubleValue=value)
            elif type_ in STRING:
                return Field(stringValue=value)
            elif type_ in BOOLEAN:
                return Field(booleanValue=value)
            elif type_ in TIMESTAMP:
                return Field(stringValue=self._format_datetime(value))
            elif type_ in BLOB:
                if isinstance(value, str):  # pragma: no cover
                    value = value.encode()
                return Field(blobValue=b64encode(value))

        return self.get_field_from_value(value)

    def get_value_from_jdbc_type(self, value: Any, jdbc_type: Optional[int]) -> Field:
        type_: Optional[JDBCType] = None
        if jdbc_type:
            try:
                type_ = JDBCType(jdbc_type)
            except ValueError:
                pass
        if type_:
            if value is None:
                return None
            elif type_ in LONG:
                return int(value)
            elif type_ in DOUBLE:
                return float(value)
            elif type_ in STRING:
                return str(value)
            elif type_ in BOOLEAN:
                return bool(value)
            elif type_ in TIMESTAMP:
                return self._format_datetime(value)
            elif type_ in BLOB:
                if isinstance(value, str):  # pragma: no cover
                    value = value.encode()
                return b64encode(value)

        return value

    def create_column_metadata_set(
        self, cursor: jaydebeapi.Cursor
    ) -> List[ColumnMetadata]:
        meta = getattr(cursor, '_meta')
        return [
            ColumnMetadata(
                arrayBaseColumnType=0,
                isAutoIncrement=meta.isAutoIncrement(i),
                isCaseSensitive=meta.isCaseSensitive(i),
                isCurrency=meta.isCurrency(i),
                isSigned=meta.isSigned(i),
                label=meta.getColumnLabel(i),
                name=meta.getColumnName(i),
                nullable=meta.isNullable(i),
                precision=meta.getPrecision(i),
                scale=meta.getScale(i),
                schema=meta.getSchemaName(i),
                tableName=meta.getTableName(i),
                type=meta.getColumnType(i),
                typeName=meta.getColumnTypeName(i),
            )
            for i in range(1, meta.getColumnCount() + 1)
        ]

    def autocommit_off(self) -> None:  # pragma: no cover
        self.connection.jconn.setAutoCommit(False)

    @staticmethod
    @abstractmethod
    def reset_generated_id(cursor: jaydebeapi.Cursor) -> None:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def last_generated_id(cursor: jaydebeapi.Cursor) -> int:
        raise NotImplementedError

    def execute(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        include_result_metadata: bool = False,
        format_records_as: str | None = None,
    ) -> ExecuteStatementResponse:
        try:
            cursor: Optional[jaydebeapi.Cursor] = None
            try:
                cursor = self.connection.cursor()

                self.reset_generated_id(cursor)
                if params:
                    cursor.execute(self.create_query(sql, params))
                else:
                    cursor.execute(str(text(sql)))
                if cursor.description:
                    column_metadata_set = self.create_column_metadata_set(cursor)
                    response = ExecuteStatementResponse(numberOfRecordsUpdated=0)

                    if format_records_as == "JSON":
                        records = self.create_json_records(cursor, column_metadata_set)
                        response.formattedRecords = json.dumps(records)
                    else:
                        response.records = self.create_unformatted_records(
                            cursor, column_metadata_set
                        )

                    if include_result_metadata:
                        response.columnMetadata = column_metadata_set

                    return response
                else:
                    rowcount: int = cursor.rowcount
                    last_generated_id: int = self.last_generated_id(cursor)
                    generated_fields: List[Field] = []
                    if last_generated_id > 0:
                        generated_fields.append(
                            self.get_field_from_value(last_generated_id)
                        )
                    return ExecuteStatementResponse(
                        numberOfRecordsUpdated=rowcount,
                        generatedFields=generated_fields,
                    )
            finally:
                if cursor:  # pragma: no cover
                    cursor.close()

        except jaydebeapi.DatabaseError as e:
            message: str = 'Unknown'
            if len(getattr(e, 'args', [])):
                message = e.args[0]
                if len(getattr(e.args[0], 'args', [])):
                    message = e.args[0].args[0]
                    if getattr(e.args[0].args[0], 'cause', None):
                        message = e.args[0].args[0].cause.message
            raise BadRequestException(str(message))

    def create_unformatted_records(self, cursor, column_metadata_set):
        column_types = [col_meta.type for col_meta in column_metadata_set]
        return [
            [
                self.get_filed_from_jdbc_type(column, column_type)
                for column, column_type in zip(row, column_types)
            ]
            for row in cursor.fetchall()
        ]

    def create_json_records(self, cursor, column_metadata_set):
        labels = [col_meta.label for col_meta in column_metadata_set]
        types = [col_meta.type for col_meta in column_metadata_set]
        rows = [self.pythonize_values(row, types) for row in cursor.fetchall()]
        return [dict(zip(labels, row)) for row in rows]

    def pythonize_values(self, values, types):
        return [self.get_value_from_jdbc_type(v, t) for v, t in zip(values, types)]

    @classmethod
    def create_connection_maker(
        cls,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user_name: Optional[str] = None,
        password: Optional[str] = None,
        engine_kwargs: Dict[str, Any] = None,
    ) -> ConnectionMaker:
        url: str = f'{cls.JDBC_NAME}://{host}:{port}/'

        if not engine_kwargs or 'JAR_PATH' not in engine_kwargs:
            raise Exception('Not Found JAR_PATH in settings')

        return connection_maker(
            cls.DRIVER,
            url,
            {"user": user_name, "password": password},
            engine_kwargs['JAR_PATH'],
        )
