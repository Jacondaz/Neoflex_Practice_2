from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.postgres.operators.postgres import PostgresOperator
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, IntegerType, FloatType, StringType, DateType
from datetime import datetime
import time

hook = PostgresHook(postgres_conn_id='postgres_default2')
postgres_uri = 'jdbc:postgresql://postgres:5432/dwh'
properties = {
    "user": "airflow",
    "password": "airflow",
    "driver": "org.postgresql.Driver"
}

schema_deal_info = StructType([
    StructField("DEAL_RK", IntegerType(), nullable=False),
    StructField("DEAL_NUM", StringType()),
    StructField("DEAL_NAME", StringType()),
    StructField("DEAL_SUM", FloatType()),
    StructField("CLIENT_RK", IntegerType(), nullable=False),
    StructField("ACCOUNT_RK", IntegerType(), nullable=False),
    StructField("AGREEMENT_RK", IntegerType(), nullable=False),
    StructField("DEAL_START_DATE", DateType()),
    StructField("DEPARTMENT_RK", IntegerType()),
    StructField("PRODUCT_RK", IntegerType()),
    StructField("DEAL_TYPE_CD", StringType()),
    StructField("effective_from_date", DateType(), nullable=False),
    StructField("effective_to_date", DateType(), nullable=False)
])

schema_product_info = StructType([
    StructField("PRODUCT_RK", IntegerType(), nullable=False),
    StructField("PRODUCT_NAME", StringType()),
    StructField("effective_from_date", DateType(), nullable=False),
    StructField("effective_to_date", DateType(),nullable=False)
])

def spark_session():
    spark = SparkSession.builder.appName("AirflowETL").config("spark.jars", "/opt/spark/jars/postgresql-42.7.3.jar").getOrCreate()
    return spark

def truncate_table():
    connection = hook.get_conn()
    cursor = connection.cursor()
    cursor.execute(f"TRUNCATE TABLE rd.product;")
    connection.commit()
    cursor.close()

def fill_deal_info():
    spark = spark_session()
    df = spark.read.csv('/opt/airflow/files/deal_info.csv', sep=',', header=True, schema=schema_deal_info)
    df.write.jdbc(url=postgres_uri, table='rd.deal_info', mode='append', properties=properties)
    spark.stop()

def fill_product_info():
    spark = spark_session()
    df = spark.read.csv('/opt/airflow/files/product_info.csv', sep=',', header=True, schema=schema_product_info)
    df.write.jdbc(url=postgres_uri, table='rd.product', mode='append', properties=properties)
    spark.stop()

def start_process(**kwargs):
    start_time = datetime.now()
    sql = """
    INSERT INTO logs.logs (start_time, status)
    VALUES (%s, %s) returning log_id
    """
    connection = hook.get_conn()
    cursor = connection.cursor()
    cursor.execute(sql, (start_time, 'Start_fill_tables'))
    log_id = cursor.fetchone()[0]
    ti = kwargs['ti']
    ti.xcom_push(key='log_id', value=log_id)
    connection.commit()
    cursor.close()
    time.sleep(5)

def end_process(**kwargs):
    end_time = datetime.now()
    sql = """
    UPDATE logs.logs
    SET end_time = %s, status = %s
    WHERE log_id = %s
    """
    connection= hook.get_conn()
    cursor = connection.cursor()
    ti = kwargs['ti']
    log_id = ti.xcom_pull(key='log_id')
    cursor.execute(sql, (end_time, 'End_fill_tables', log_id))
    connection.commit()
    cursor.close()

default_args={
    'owner':'airflow',
    'start_date':datetime(2024, 8, 4),
    'retries': 1
}

dag = DAG(
    "refresh_loan_holiday",
    default_args=default_args,
    schedule_interval='@daily'
)

start = PythonOperator(
    task_id='Start',
    python_callable=start_process,
    provide_context=True,
    dag=dag
)

end = PythonOperator(
    task_id='End',
    python_callable=end_process,
    provide_context=True,
    dag=dag
)

truncate = PythonOperator(
    task_id='Truncate_product',
    python_callable=truncate_table,
    provide_context=True,
    dag=dag
)

fill_deal = PythonOperator(
    task_id='Fill_deal_info',
    python_callable=fill_deal_info,
    provide_context=True,
    dag=dag
)

fill_product = PythonOperator(
    task_id='Fill_product_info',
    python_callable=fill_product_info,
    provide_context=True,
    dag=dag
)

fill_loan_holiday_info = PostgresOperator(
    task_id = 'fill_loan_holiday_info',
    postgres_conn_id = 'postgres_default2',
    sql = """
    call dm.refresh_loan_holiday_info();
    """,
    dag=dag
)
start >> [truncate, fill_deal]
truncate >> fill_product
[fill_deal, fill_product] >> fill_loan_holiday_info >> end


