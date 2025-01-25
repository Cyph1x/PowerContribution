import json
import logging
import pandas as pd
import numpy as np
import asyncio
import os
from datetime import datetime, timedelta
from tapo import ApiClient
from tapo.requests import EnergyDataInterval
from Ovo import Ovo
import matplotlib
import matplotlib.pyplot as plt

logging.basicConfig(level=logging.INFO)

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
        df = df[df["time"] >= min_time]
    if max_time is not None:
        df = df[df["time"] <= max_time]
    max_time = df["time"].max()
    min_time = df["time"].min()

    # Convert min_time and max_time to datetime
    min_time = pd.to_datetime(min_time, unit='s') + timedelta(hours=10) # Adjust for timezone
    max_time = pd.to_datetime(max_time, unit='s') + timedelta(hours=10) # Adjust for timezone
    fig, ax = plt.subplots(figsize=(20,10))

    date_time = pd.to_datetime(df['time'], unit='s') + timedelta(hours=10) # Adjust for timezone

    bottom = np.zeros(len(df))
    for column in df.columns:
        if column == 'time':
            continue
        p = ax.bar(date_time, df[column],0.03,bottom=bottom, label=column)
        bottom += df[column]

    ax.set_title("Hourly power usage contribution { " + str(min_time) + " to " + str(max_time) + " }")
    ax.set_xlabel("Time")
    ax.set_ylabel("Power usage (kWh)")

    ax.xaxis.set_major_locator(matplotlib.dates.HourLocator(interval=1))
    ax.set_xlim([min_time, max_time])
    plt.xticks(rotation=90)

    ax.legend(loc="upper right")

    plt.show()

async def main():

    client = ApiClient(os.getenv("TAPO_USERNAME"), os.getenv("TAPO_PASSWORD"))

    # load the devices
    with open("devices.json", "r") as f:
        device_list = json.load(f)

    # Get Ovo data
    ovo = Ovo()
    ovo.login(os.getenv("OVO_USERNAME"), os.getenv("OVO_PASSWORD"))
    ovo_data = ovo.get_hourly_data(os.getenv("OVO_ACCOUNT_ID"))

    devices = []
    for device in device_list:
        device_type = device['type']
        device_ip = device['ip']
        match device_type.lower():
            case "p110":
                devices.append(await client.p110(device_ip))
            case _:
                logging.error(f"Unknown device type: {device_type}")
                exit(1)

    merged_energy_data = ovo_data
    for device in devices:
        device_energy_data = await getEnergyData(device)
        merged_energy_data = merged_energy_data.merge(device_energy_data, on='time', how='outer')
    merged_energy_data = merged_energy_data.fillna(0)

    # Remove measured power from the total power
    CL1_col = "E1" # normal power usage is summed to this column
    CL2_col = "E2" # solar power usage is summed to this column
    for col in merged_energy_data.columns:
        if col != "time" and col != CL1_col and col != CL2_col:
            merged_energy_data[CL1_col] -= merged_energy_data[col]

    # Rename fields
    merged_energy_data = merged_energy_data.rename(columns={CL1_col: "Unknown",CL2_col: "CL2"})

    # plot the last 7 days
    ovo_end_time = ovo_data["time"].max() # Most likely ovo will be the last to report
    plotUsage(merged_energy_data, min_time=ovo_end_time - timedelta(days=3).total_seconds(), max_time=ovo_end_time)

    #plotUsage(merged_energy_data, min_time=1737432000, max_time=1737637200)



if __name__ == "__main__":
    asyncio.run(main())