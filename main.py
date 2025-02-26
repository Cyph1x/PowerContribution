import pandas as pd
import numpy as np
import asyncio
import os
from datetime import datetime, timedelta
from tapo.requests import EnergyDataInterval
from Ovo import Ovo
import matplotlib.pyplot as plt
import logging
from TP_Cloud import TP_Cloud, timezone
import pytz
from tabulate import tabulate

logging.basicConfig(level=5)

async def getEnergyData(device, rename=True):

    logging.info("Fetching energy data")

    device_info = await device.get_device_info()
    nickname = device_info.nickname
    logging.info(f"Device name: {nickname}")

    end_day = datetime.today()
    start_day = end_day - timedelta(days=30)

    # split into 7 day intervals
    intervals = []
    while start_day < end_day:
        intervals.append((start_day, min(start_day + timedelta(days=7),end_day)))
        start_day += timedelta(days=7)

    # Fetch all hourly data
    energy_data_hourly = []
    for interval in intervals:
        energy_data_hourly.append(await device.get_energy_data(EnergyDataInterval.Hourly, interval[0], interval[1]))

    # Calculate the timestamp for each reading in each interval
    times = []
    for energy_data_result in energy_data_hourly:
        start_time = energy_data_result.start_timestamp
        end_time = energy_data_result.end_timestamp

        times.append(np.arange(start_time, end_time, 3600))

    # Extract the hourly readings
    energy_usage = [energy_data_result.data for energy_data_result in energy_data_hourly]
    energy_usage = np.concat([energy_data_result.data for energy_data_result in energy_data_hourly])
    energy_times = np.concatenate(times)

    # Create a pandas dataframe
    df = pd.DataFrame({
        "time": energy_times,
        "energy_usage": energy_usage
    }, index=energy_times)
    # Remove duplicate timestamps. (This can happen when intervals overlap but the reading for a timestamp shouldn't change)
    df = df[~df.index.duplicated(keep='first')]

    df = df.sort_index() # Just in case

    # convert to kwh
    df['energy_usage'] = df['energy_usage'] / 1000

    if rename:
        df = df.rename(columns={"energy_usage": nickname})
    return df

def plotUsage(df, min_time=None, max_time=None):
    if min_time is not None:
        df = df[df.index >= min_time]
    if max_time is not None:
        df = df[df.index <= max_time]
    max_time = df.index.max()
    min_time = df.index.min()

    # Convert min_time and max_time to datetime
    min_time = pd.to_datetime(min_time, unit='s') + timedelta(hours=10) # Adjust for timezone
    max_time = pd.to_datetime(max_time, unit='s') + timedelta(hours=10) # Adjust for timezone
    date_time = pd.to_datetime(df.index, unit='s') + timedelta(hours=10) # Adjust for timezone

    # create a stacked bar chart
    fig, ax = plt.subplots()
    ax.bar(date_time, df['Unknown'], label='Unknown')
    ax.bar(date_time, df['Joshua'], label='Joshua', bottom=df['Unknown'])
    ax.bar(date_time, df['Jack'], label='Jack', bottom=df['Unknown'] + df['Joshua'])
    ax.bar(date_time, df['CL2'], label='CL2', bottom=df['Unknown'] + df['Joshua'] + df['Jack'])

    ax.set_xlim(min_time, max_time)
    ax.set_ylabel("Power Usage (kWh)")
    ax.set_xlabel("Time")
    ax.legend()
    plt.show()



async def main():

    # Get Ovo data
    ovo = Ovo()
    ovo.login(os.getenv("OVO_USERNAME"), os.getenv("OVO_PASSWORD"))
    ovo_data = ovo.getDailyEnergyData(os.getenv("OVO_ACCOUNT_ID"))

    # login to the TP-Link cloud
    tp_cloud = TP_Cloud(os.getenv("TAPO_USERNAME"), os.getenv("TAPO_PASSWORD"))
    tp_cloud.login()
    devices = tp_cloud.getThingsList()

    end_time = datetime.now(pytz.UTC)
    start_time = end_time - timedelta(days=60)
    end_time = end_time.timestamp()
    start_time = start_time.timestamp()

    device_energy_usage = {}
    for device_id in devices:
        device_energy_usage[device_id] = tp_cloud.getDailyEnergyData(device_id, start_time, end_time)

    # resample the data to match the ovo data
    resample_interval = 60*60
    """for device_id in device_energy_usage:
        print(device_id)
        energy_usage = device_energy_usage[device_id]

        # parse the index from a epoch timestamp to a datetime
        energy_usage.index = pd.to_datetime(energy_usage.index, unit='s')


        time_diff = energy_usage.index.to_series().diff().dt.total_seconds()
        # Backwards fill the first value (would only cause an issue if the data is a week long, which it's not)
        time_diff = time_diff.bfill()

        # Divide the energy usage by the time difference to get the to get the resampled power usage
        resampled_energy_usage = energy_usage['energy_usage'] / (time_diff / 60)

        # resample the data to be minutely
        resampled_energy_usage = resampled_energy_usage.resample('T').ffill()

        # as a sanity check, ensure that the total energy usage is the same
        assert np.sum(energy_usage['energy_usage']) == np.sum(resampled_energy_usage)

        device_energy_usage[device_id] = resampled_energy_usage"""

    # Merge the data

    CL1_col = "E1" # normal power usage is summed to this column
    CL2_col = "E2" # controlled load power usage is summed to this column

    cl1_usage = ovo_data[CL1_col]
    cl2_usage = ovo_data[CL2_col]

    joshua_usage = device_energy_usage['802209B6E9AED495039F1C2A2846494D233FE4E0'] # my smart plug id
    jack_usage = device_energy_usage['802203E4E5E493B4C102F78AFD96B43323256940'] # Jack's smart plug id

    # Modifiers

    # Joshua uses the fan almost 24/7
    # Uss 60Wh (https://www.bunnings.com.au/hpm-1220mm-white-hangsure-ceiling-fan_p4441507)
    joshua_usage += (60 / 1000) * 24

    # Joshua uses the tv for 1.31 hours a day (using home assistant logging)
    # Uses 125Wh
    joshua_usage += (125 / 1000) * 1.31

    # The Fridge uses 346kWh per year
    joshua_usage += (346 / 365) / 2 # (Joshua and Tillie)

    # Assuming the lights in joshua's room (1), kitchen (2), living room (2), bathroom (1) are on for 12 hours a day
    joshua_usage += (10 /1000) * 6 * 12 / 2 # (Joshua and Tillie)

    # Rename the dataframes
    joshua_usage = joshua_usage.rename(columns={"energy_usage": "Joshua"})
    jack_usage = jack_usage.rename(columns={"energy_usage": "Jack"})
    cl1_usage = cl1_usage.rename(columns={"energy_usage": 'Unknown'})
    cl2_usage = cl2_usage.rename(columns={"energy_usage": 'CL2'})

    # Merge the data
    merged_energy_data = cl1_usage.copy()
    merged_energy_data = merged_energy_data.merge(cl2_usage, left_index=True, right_index=True, how='outer')
    merged_energy_data = merged_energy_data.merge(joshua_usage, left_index=True, right_index=True, how='outer')
    merged_energy_data = merged_energy_data.merge(jack_usage, left_index=True, right_index=True, how='outer')
    merged_energy_data = merged_energy_data.fillna(0)

    # Subtract known power usage from the Unknown column
    measured_power = ["Joshua", "Jack"]
    for col in measured_power:
        merged_energy_data["Unknown"] -= merged_energy_data[col]

    start_billing_period = datetime(2025, 1, 16, tzinfo=timezone)
    end_billing_period = datetime(2025, 2, 16, tzinfo=timezone)

    start_billing_period = start_billing_period.astimezone(pytz.utc)
    end_billing_period = end_billing_period.astimezone(pytz.utc)

    start_billing_period = start_billing_period.timestamp()
    end_billing_period = end_billing_period.timestamp()

    # plot the billing period
    #plotUsage(merged_energy_data, min_time=start_billing_period, max_time=end_billing_period)

    # Generate some stats
    merged_energy_data = merged_energy_data[merged_energy_data.index >= start_billing_period]
    merged_energy_data = merged_energy_data[merged_energy_data.index <= end_billing_period]

    print("Total usage:")
    print(tabulate(merged_energy_data.sum().to_frame(), headers = 'keys', tablefmt = 'psql'))
    print("Average usage:")
    print(tabulate(merged_energy_data.mean().to_frame(), headers = 'keys', tablefmt = 'psql'))

    costs = merged_energy_data.sum()
    anytime_rate = 0.2288
    cl2_rate = 0.2376
    costs['Unknown'] *= anytime_rate
    costs['CL2'] *= cl2_rate
    costs['Joshua'] *= anytime_rate
    costs['Jack'] *= anytime_rate

    print("Total cost:")
    print(tabulate(costs.to_frame(), headers = 'keys', tablefmt = 'psql'))


if __name__ == "__main__":
    asyncio.run(main())