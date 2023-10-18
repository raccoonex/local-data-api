# local-data-api - Local Data API for AWS Aurora Serverless Data API


This is fork of **koxudaxi**'s [local-data-api](https://github.com/koxudaxi/local-data-api) with added support for formatting records as JSON using the `formatRecordsAs` parameter (see [rds-data documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds-data/client/execute_statement.html))/

Currently tested only with the PostgreSQL database.

## What's AWS Aurora Serverless's Data API?
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html

## How does local-data-api work?
local-data-api is "proxy server" to real databases.

The API converts RESTful request to SQL statements.

## How to insert array data into PostgreSQL
DataAPI have not support inserting array data with `SqlParameter` yet.
But, @ormu5 give us this workaround to insert array data.
```sql
insert into cfg.attributes(id, type, attributes)
values(:id, :type, cast(:attributes as text[]));
```
where the value for attributes parameter is a string properly formatted as Postgres array, e.g., `'{"Volume","Material"}'`.

Thanks to @ormu5.

## How to use this image

The easiest way to run data-api locally is to use this docker-compose configuration:

```
version: '3'
services:
  local-data-api:
    build: https://github.com/raccoonex/local-data-api.git
    restart: unless-stopped
    environment:
      ENGINE: PostgreSQLJDBC
      POSTGRES_HOST: db
      POSTGRES_PORT: 5432
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: example
      RESOURCE_ARN: 'arn:aws:rds:us-east-1:123456789012:cluster:dummy'
      SECRET_ARN: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:dummy'
    depends_on:
      - db

  db:
    image: postgres:13-bullseye
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: example
      POSTGRES_DB: skills
    volumes: 
      - db:/var/lib/postgresql/data

  pg-admin:
    image: dpage/pgadmin4
    restart: unless-stopped
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@pgadmin.com
      PGADMIN_DEFAULT_PASSWORD: admin
    ports:
      - 5050:80
    volumes:
      - pgadmin-data:/var/lib/pgadmin

volumes:
  db:
  pgadmin-data:

```

### docker-compose with Python's aws-sdk client(boto3) 
1. start local-data-api containers
```bash
$ docker-compose up -d
```

2. change a endpoint to local-data-api in your code. 
```bash
$ ipython
```
```python
In [1]: import boto3; client = boto3.client('rds-data', endpoint_url='http://127.0.0.1:8000', aws_access_key_id='aaa',  aws_secret_access_key='bbb') 
```

If a table has some records, then the local-data-api can run `select`
```python
In [2]: client.execute_statement(resourceArn='arn:aws:rds:us-east-1:123456789012:cluster:dummy', secretArn='arn:aws:secretsmanager:us-east-1:123456789012:secret:dummy', sql='select * from users', database='test')
```
```python
Out[3]: {'ResponseMetadata': {'HTTPStatusCode': 200,
 'HTTPHeaders': {'date': 'Sun, 09 Jun 2019 18:35:22 GMT',
 'server': 'uvicorn',
 'content-length': '492',
 'content-type': 'application/json'},
 'RetryAttempts': 0},
 'numberOfRecordsUpdated': 0,
 'records': [[{'longValue': 1}, {'stringValue': 'ichiro'}, {'longValue': 17}],
  [{'longValue': 2}, {'stringValue': 'ken'}, {'longValue': 20}],
  [{'longValue': 3}, {'stringValue': 'lisa'}, {'isNull': True}],}
```

Or formatted as JSON
```python
In [4]: client.execute_statement(resourceArn='arn:aws:rds:us-east-1:123456789012:cluster:dummy', secretArn='arn:aws:secretsmanager:us-east-1:123456789012:secret:dummy', sql='select * from users', database='test', formatRecordsAs="JSON")
```
```python
Out[5]: {'ResponseMetadata': {'HTTPStatusCode': 200,
 'HTTPHeaders': {'date': 'Sun, 09 Jun 2019 18:35:22 GMT',
 'server': 'uvicorn',
 'content-length': '492',
 'content-type': 'application/json'},
 'RetryAttempts': 0},
 'numberOfRecordsUpdated': 0,
 'records': [{"id": 1, "name": "ichoro", "items": 17},
  {"id": 2, "name": "ken", "items": 20}],}
```

## License

local-data-api is released under the MIT License. http://www.opensource.org/licenses/mit-license
