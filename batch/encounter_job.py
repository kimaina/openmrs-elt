# coding: utf-8

import time
from datetime import datetime
from pyspark.sql import SparkSession, Row, SQLContext, Window
from pyspark.sql.types import StructType, StringType, StructField, BooleanType, IntegerType, ArrayType, TimestampType, \
    DoubleType
from pyspark import SparkContext
import pyspark.sql.functions as f
from common.utils import PipelineUtils
from common.encounter_helper import EncounterHelper
from common.cassandra import CassandraUtils

class EncounterJob(PipelineUtils):

    # responsible for ingesting obs with non-null encounters
    def ingest_obs_with_encounter(self):
        obs = super().getDataFromMySQL('obs_with_encounter', {
            'partitionColumn': 'obs_id',
            'fetchsize': 100000,
            'lowerBound': 1,
            'upperBound': 300000000,
            'numPartitions': 5000})
            #.repartition(100,f.col("patient_id"), f.col("encounter_id"))
        return EncounterHelper.sanitize_obs(obs)

    # responsible for ingesting obs with null encounters
    def ingest_obs_without_encounter(self):
        obs = super().getDataFromMySQL('obs_with_null_encounter', {
            'partitionColumn': 'obs_id',
            'fetchsize': 100000,
            'lowerBound': 1,
            'upperBound': 300000000,
            'numPartitions': 5000}) \
            .withColumn('encounter_id', f.col('obs_id') + 10000000000) \
            .withColumn('order_id', f.lit('null')) \
            .withColumn('order_concept_id', f.lit('null'))
        return EncounterHelper.sanitize_obs(obs)

    # responsible for ingesting orders
    def ingest_orders(self):
        orders = super().getDataFromMySQL('encounter_orders', {
            'partitionColumn': 'order_id',
            'fetchsize': 5083,
            'lowerBound': 1,
            'upperBound': 300000,
            'numPartitions': 24})
        return EncounterHelper.sanitize_orders(orders)

    # responsible for saving and optimizing delta tables
    def sinkBatch(self, df, table):
        config = super().getConfig()['storage']
        tableConfig = config['tables'][table]
        if config["db"]=="delta":
             df\
                .repartition(f.col("location_id"))\
                .write.format("delta").mode("overwrite")\
                .partitionBy(tableConfig["partitionBy"])\
                .save(tableConfig["path"])

        elif config["db"]=="cassandra":
            CassandraUtils.sinkToCassandra(df.repartition(f.col("patient_id"),f.col("encounter_id")), 
                                    table,
                                     mode="overwrite")


        #super().spark.sql("OPTIMIZE tableName ZORDER BY (my_col)")

    # start spark job
    def run(self):

        start_time = datetime.utcnow()
        print("---Encounter Batch Started--- ")
        print("Starting Time: " + time.ctime())

        try:

            # ingest all components
            orders = self.ingest_orders()

            # union obs and join
            all_obs = self.ingest_obs_with_encounter().union(self.ingest_obs_without_encounter())
    
            # sink to delta/cassandra
            self.sinkBatch(EncounterHelper.join_obs_orders(all_obs,orders),'flat_obs_orders' )

        except Exception as e:
            print("An unexpected error occurred: ", e)
            raise 

        # uncomment to save individual tables
        #self.sinkBatch(all_obs, 'flat_obs')
        #self.sinkBatch(orders, 'flat_orders')
        end_time = datetime.utcnow()
        print("Batch Ending Time: " + time.ctime())
        print("Took {0} minutes".format((end_time - start_time).total_seconds()/60))


        