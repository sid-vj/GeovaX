"""Apache Sedona (Spark) job for distributed Pan-India geospatial harmonisation.

This job is intended to be run on a Spark cluster via spark-submit, 
taking a district LGD code as input to process millions of polygons 
(e.g., Google Open Buildings vs State Cadastre) using distributed spatial joins.
"""
import argparse
import sys
# from pyspark.sql import SparkSession
# from sedona.spark import *
# from sedona.core.formatMapper.shapefileParser import ShapefileReader

def get_spark_session(app_name="SamanvaySedona"):
    """Initialize a Spark Session with Sedona extensions."""
    # return SparkSession.builder \
    #     .appName(app_name) \
    #     .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer") \
    #     .config("spark.kryo.registrator", "org.apache.sedona.core.serde.SedonaKryoRegistrator") \
    #     .getOrCreate()
    pass

def harmonise_district(district_lgd: str):
    """Run spatial harmonisation for the district."""
    print(f"[Spark] Starting Sedona spatial join for district {district_lgd}")
    # spark = get_spark_session(f"Harmonise_{district_lgd}")
    # sedona = SedonaContext.create(spark)
    
    # 1. Load Data
    # cadastre_df = sedona.read.format("geoparquet").load(f"s3://samanvay-datalake/raw/cadastre/{district_lgd}.parquet")
    # buildings_df = sedona.read.format("geoparquet").load(f"s3://samanvay-datalake/raw/gobi/{district_lgd}.parquet")
    
    # 2. Distributed Spatial Join (Intersection)
    # cadastre_df.createOrReplaceTempView("cadastre")
    # buildings_df.createOrReplaceTempView("buildings")
    
    # query = """
    # SELECT c.ulpin, b.gobi_id, ST_Intersection(c.geometry, b.geometry) as geom, ST_Area(ST_Intersection(c.geometry, b.geometry)) as intersect_area
    # FROM cadastre c, buildings b
    # WHERE ST_Intersects(c.geometry, b.geometry)
    # """
    # joined_df = sedona.sql(query)
    
    # 3. Write Output
    # joined_df.write.format("geoparquet").mode("overwrite").save(f"s3://samanvay-datalake/harmonised/{district_lgd}.parquet")
    print(f"[Spark] Completed district {district_lgd}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAMANVAY Sedona Harmonisation Job")
    parser.add_argument("--district", required=True, help="LGD Code of the district to harmonise")
    args = parser.parse_args()
    
    harmonise_district(args.district)
