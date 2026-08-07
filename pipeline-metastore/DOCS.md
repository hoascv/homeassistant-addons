# Pipeline Metastore

A Hive **standalone metastore** for the data-pipeline stack. It is the catalog:
the thing that knows `gym_tracker.workout_logs` is a Delta table at
`s3a://lakehouse/gym_tracker/workout_logs`, so you can write SQL against a name
instead of a path.

Part of the data pipeline. Start it after Postgres and MinIO, before Spark:
`postgres → minio → metastore → spark → airflow → notebook`.

> **Optional.** The pipeline works without it — every bundled DAG and
> `lakehouse.py` address tables by path and always will. Add this when you want
> `spark.sql("SELECT … FROM gym_tracker.workout_logs")`, or a second engine that
> can find the tables without importing a Python module.

## What it does

- Runs Apache Hive's standalone metastore 4.1.0, serving the Thrift API on host
  port **9083**.
- Stores its catalog in a **`metastore` database on the Pipeline Postgres
  add-on**, created on first start along with a `hive` role.
- Resolves `s3a://` locations against MinIO, so tables can live in the lakehouse
  bucket rather than on local disk.

## Configuration

- **postgres_host** / **postgres_port**: the Pipeline Postgres add-on. Defaults
  to the Supervisor bridge gateway, `172.30.32.1:5432`.
- **postgres_admin_user** / **postgres_admin_password** / **postgres_admin_db**:
  an existing login on that Postgres, used **only** to create the metastore's
  role and database on first start. The Pipeline Postgres main user (default
  `pipeline`) is the intended value — it is that instance's superuser.
- **metastore_db** / **metastore_db_user** / **metastore_db_password**: the
  database and role this add-on creates and then uses for itself. Set the
  password before the first start; changing it afterwards means changing it in
  Postgres too (`ALTER ROLE hive PASSWORD …`).
- **warehouse_dir**: where tables go when they are created without an explicit
  `LOCATION`. Defaults to `s3a://lakehouse/warehouse`.
- **minio_endpoint** / **minio_access_key** / **minio_secret_key**: same values
  as the Spark and Airflow add-ons. The metastore needs them because it creates
  a table's directory itself at `CREATE TABLE` time.
- **heap_mb**: JVM heap. 512 MB is comfortable for a home-sized catalog.

## Pointing Spark at it

The metastore does nothing until something uses it. In the **Pipeline Spark**
add-on (1.4.0+), set:

```
metastore_uris: thrift://172.30.32.1:9083
```

and restart it. Leaving it empty keeps the previous behaviour — a session-local
catalog, tables by path only.

> **The first query after enabling this needs internet.** Spark's built-in Hive
> client is 2.3.10 and cannot talk to a 4.1.0 metastore, so Spark is configured
> to fetch a matching 4.1.0 client from Maven into an isolated classloader. It is
> cached under `/data/spark` afterwards, so this happens once.

The Notebook add-on needs no change: it talks to Spark Connect, and the catalog
lives on the Spark side of that connection.

## Registering the tracker tables

The DAGs write Delta tables by path and do not register them. Do it once, from
JupyterLab or any Spark session:

```python
spark.sql("CREATE DATABASE IF NOT EXISTS gym_tracker")
spark.sql("""
    CREATE TABLE IF NOT EXISTS gym_tracker.workout_logs
    USING DELTA LOCATION 's3a://lakehouse/gym_tracker/workout_logs'
""")
spark.sql("SELECT count(*) FROM gym_tracker.workout_logs").show()
```

`CREATE TABLE … LOCATION` over an existing Delta directory registers it without
moving or rewriting anything, and Delta keeps the schema in its own transaction
log — so a tracker gaining a column does not need the table re-registered.

Note that these register the **raw** tables, whose payload is a JSON string.
`lakehouse.py` still owns the typed reading; the two are complementary, and
`lakehouse.table()` keeps working exactly as before.

## Notes

- **Version pins are load-bearing.** Metastore 4.1.0 is the newest release Spark
  4.1 supports; it is compiled for Java 17 and built against Hadoop 3.4.1, both
  of which the image provides. Do not bump one of the three alone.
- The catalog is only metadata. Dropping a table registered with `LOCATION`
  leaves the Delta files untouched; losing the metastore database loses names,
  not data, and the `CREATE TABLE` statements above rebuild it.
- Backups: the catalog is a normal Postgres database, so it is covered by
  whatever you already do for the pipeline database.
