import boto3
from botocore.exceptions import ClientError
import sys
import backoff
import pymysql
import click
from datetime import datetime, timedelta, timezone

MAX_TRIES = 5
PERIOD = 360
UNIT = 'Percent'

class EC2BotoWrapper:
    def __init__(self):
        self.client = boto3.client("ec2")

    @backoff.on_exception(backoff.expo,
                          ClientError,
                          max_tries=MAX_TRIES)
    def describe_regions(self):
        return self.client.describe_regions()


class CwBotoWrapper():
    def __init__(self):
        self.client = boto3.client('cloudwatch')

    @backoff.on_exception(backoff.expo,
                          ClientError,
                          max_tries=MAX_TRIES)
    def list_metrics(self, *args, **kwargs):
        return self.client.list_metrics(*args, **kwargs)

    @backoff.on_exception(backoff.expo,
                          ClientError,
                          max_tries=MAX_TRIES)
    def put_metric_data(self, *args, **kwargs):
        return self.client.put_metric_data(*args, **kwargs)

    @backoff.on_exception(backoff.expo,
                          ClientError,
                          max_tries=MAX_TRIES)
    def get_metric_stats(self, *args, **kwargs):
        return self.client.get_metric_statistics(*args, **kwargs)


class RDSBotoWrapper:
    def __init__(self, **kwargs):
        self.client = boto3.client("rds", **kwargs)

    @backoff.on_exception(backoff.expo,
                          ClientError,
                          max_tries=MAX_TRIES)
    def describe_db_instances(self):
        return self.client.describe_db_instances()


def send_an_email(to_addr, from_addr, primary_keys_message, region):
    client = boto3.client('ses', region_name=region)
    message = ''
    flag =True

    DaysRemainingLabel = ''
    for item in range(len(primary_keys_message)):
        if flag:
            DaysRemaining = primary_keys_message[item]['remaining_days'] if "remaining_days" in primary_keys_message[
                item] else None
            if DaysRemaining is not None:
                flag = False
                DaysRemainingLabel = """"<th>Remaining Days</th>"""

        message += """
            <tr><td>{Database}</td>
            <td>{Table}</td>
            <td>{UsedPercentage}</td>
            <td>{DaysRemaining}</td>
            </tr>""".format(
            Database=primary_keys_message[item]['database_name'],
            Table=primary_keys_message[item]['table_name'],
            UsedPercentage=primary_keys_message[item]['percentage_of_PKs_consumed'],
            DaysRemaining=primary_keys_message[item]['remaining_days'] if "remaining_days" in primary_keys_message[item] else ''
        )

    message += """</table>"""

    header = """
        <p>Hello,</p>
        <p>Primary keys of these table exhausted soon</p>
        <table style='width:100%'>
          <tr style='text-align: left'>
            <th>Database</th>
            <th>Table</th>
            <th>Usage Percentage</th>
            <th>{DaysRemainingLabel}</th>
          </tr>
        """.format(
        DaysRemainingLabel=DaysRemainingLabel
    )
    message = header + message
    client.send_email(
        Source=from_addr,
        Destination={
            'ToAddresses': [
                to_addr
            ]
        },
        Message={
            'Subject': {
                'Data': 'Primary keys of these table would be exhausted soon',
                'Charset': 'utf-8'
            },
            'Body': {
                'Html':{
                    'Data': message,
                    'Charset': 'utf-8'
                }
            }
        }
    )


def get_rds_from_all_regions():
    """
    Gets a list of RDS instances across all the regions and deployments in AWS

    :returns:
    list of all RDS instances across all the regions
        [
            {
                'name': name of RDS,
                'Endpoint': Endpoint of RDS
                'Port': Port of RDS
            }
        ]
    name (string)
    Endpoint (string)
    Port (string)
    """
    client_region = EC2BotoWrapper()
    rds_list = []
    try:
        regions_list = client_region.describe_regions()
    except ClientError as e:
        print("Unable to connect to AWS with error :{}".format(e))
        sys.exit(1)
    for region in regions_list["Regions"]:
        client = RDSBotoWrapper(region_name=region["RegionName"])
        response = client.describe_db_instances()
        for instance in response.get('DBInstances'):
            temp_dict = dict()
            temp_dict["name"] = instance["DBInstanceIdentifier"]
            temp_dict["Endpoint"] = instance.get("Endpoint").get("Address")
            temp_dict["Port"] = instance.get("Port")
            rds_list.append(temp_dict)
    return rds_list


def check_primary_keys(rds_list, username, password, environment, deploy):
    """
    :param rds_list:
    :param username:
    :param password:

    :returns:
         Return list of all tables that cross threshold limit
              [
                  {
                    "name": "string",
                    "db": "string",
                    "table": "string",
                    "size": "string",
                  }
              ]
    """
    cloudwatch = CwBotoWrapper()
    metric_name = 'used_key_space'
    namespace = "rds-primary-keys/{}-{}".format(environment, deploy)
    try:
        table_list = []
        metric_data = []
        tables_reaching_exhaustion_limit = []
        for item in rds_list:
            rds_host_endpoint = item["Endpoint"]
            rds_port = item["Port"]
            connection = pymysql.connect(host=rds_host_endpoint,
                                         port=rds_port,
                                         user=username,
                                         password=password)
            # prepare a cursor object using cursor() method
            cursor = connection.cursor()
            # execute SQL query using execute() method.
            # this query will return the tables with usage in percentage, result is limited to 10
            cursor.execute("""
            SELECT
                table_schema,
                table_name,
                column_name,
                column_type,
                auto_increment,
                max_int,
                ROUND(auto_increment/max_int*100,2) AS used_pct
            FROM
                (
                 SELECT
                    table_schema,
                    table_name,
                    column_name,
                    column_type,
                    auto_increment,
                    pow
                        (2,
                            case data_type
                            when 'tinyint' then 7
                            when 'smallint' then 15
                            when 'mediumint' then 23
                            when 'int' then 31
                            when 'bigint' then 63
                            end
                        +(column_type like '% unsigned'))-1
                    as max_int
                 FROM
                    information_schema.tables t
                    JOIN information_schema.columns c
                        USING (table_schema,table_name)
                        WHERE t.table_schema not in ('mysql','information_schema','performance_schema')
                        AND t.table_type = 'base table'
                        AND c.extra LIKE '%auto_increment%'
                        AND t.auto_increment IS NOT NULL
                 )
            TMP ORDER BY used_pct desc
            LIMIT 10;
            """)
            rds_result = cursor.fetchall()
            cursor.close()
            connection.close()
            for table in rds_result:
                table_data = {}
                if table[6] > 70:
                    metric_data.append({
                        'MetricName': metric_name,
                        'Dimensions': [{
                            "Name": item["name"],
                            "Value": table[1]
                        }],
                        'Value': table[6],  # percentage of the usage of primary keys
                        'Unit': UNIT
                    })
                    table_data["database_name"] = item['name']
                    table_data["table_name"] = table[1]
                    table_data["percentage_of_PKs_consumed"] = table[6]
                    remaining_days = get_metrics_and_calcuate_diff(namespace, metric_name, item["name"], table[1], table[6])
                    if remaining_days:
                        table_data["remaining_days"] = remaining_days
                    tables_reaching_exhaustion_limit.append(table_data)
            if len(metric_data) > 0:
                cloudwatch.put_metric_data(Namespace=namespace, MetricData=metric_data)
        return tables_reaching_exhaustion_limit
    except Exception as e:
        print("Please see the following exception ", e)
        sys.exit(1)


def get_metrics_and_calcuate_diff(namespace, metric_name, dimension, value, current_consumption):
    cloudwatch = CwBotoWrapper()
    res = cloudwatch.get_metric_stats(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[
            {
                'Name': dimension,
                'Value': value
            },
        ],
        StartTime=datetime.utcnow() - timedelta(days=180),
        EndTime=datetime.utcnow(),
        Period=86400,
        Statistics=[
            'Maximum',
        ],
        Unit=UNIT
    )
    datapoints = res["Datapoints"]
    days_remaining_before_exhaustion = ''
    if len(datapoints) > 0:
        max_value = max(datapoints, key=lambda x: x['Timestamp'])
        time_diff = datetime.now(timezone.utc) - max_value["Timestamp"]
        last_max_reading = max_value["Maximum"]
        consumed_keys_percentage = 100 - current_consumption
        print("consuemd keys percentage: ",consumed_keys_percentage)
        print(consumed_keys_percentage, current_consumption, last_max_reading, time_diff.days)
        if current_consumption > last_max_reading:
            current_usage = current_consumption - last_max_reading
            print("current_consumption: ",current_usage)
            no_of_days = time_diff.days
            print("No of days: ", no_of_days)
            increase_over_time_period = current_usage/no_of_days
            print("increaes over time period i,e current usage/no.of days", increase_over_time_period)
            days_remaining_before_exhaustion = consumed_keys_percentage/increase_over_time_period
            print("days remaing before exhaustion : ",days_remaining_before_exhaustion)
            print("days remaining for {db} db are {days}".format(db=value,
                                                                 days=days_remaining_before_exhaustion))
    return days_remaining_before_exhaustion




@click.command()
@click.option('--username', envvar='USERNAME', required=True)
@click.option('--password', envvar='PASSWORD', required=True)
@click.option('--environment', '-e', required=True)
@click.option('--deploy', '-d', required=True,
              help="Deployment (i.e. edx or edge)")
@click.option('--region', multiple=True, help='Default AWS region')
@click.option('--recipient', multiple=True, help='Recipient Email address')
@click.option('--sender', multiple=True, help='Sender email address')
@click.option('--rdsignore', '-i', multiple=True, help='RDS name tags to not check, can be specified multiple times')
def controller(username, password, environment, deploy, region, recipient, sender, rdsignore):
    """
    calls other function and calculate the results
    :param username: username for the RDS.
    :param password: password for the RDS.
    :return: None
    """
    # get list of all the RDSes across all the regions and deployments
    rds_list = get_rds_from_all_regions()
    filtered_rds_list = list(filter(lambda x: x['name'] not in rdsignore, rds_list))
    table_list = check_primary_keys(filtered_rds_list, username, password, environment, deploy)
    if len(table_list) > 0:
        send_an_email(recipient[0], sender[0], table_list, region[0])
    sys.exit(0)


if __name__ == "__main__":
    controller()