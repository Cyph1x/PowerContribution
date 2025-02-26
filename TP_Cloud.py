import requests
import uuid
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import math
import pytz
wap_url = "https://n-wap-gw.tplinkcloud.com"
app_version = "3.8.509"
timezone = pytz.timezone('Australia/Brisbane')

class TP_Cloud:

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.term_uuid = uuid.uuid4().hex.upper()
        self.token = None
        self.nickname = None
        self.account_id = None
        self.logged_in = False
        self.region_code = None
        self.appServiceUrl = None

    def login(self):
        body = {
            "method": "login",
            "params": {
                #"appType": "TP-Link_Tapo_Android",
                "appType": "Kasa_Android",
                #"appVersion": app_version,
                "cloudUserName": self.username,
                "cloudPassword": self.password,
                #"platform": "Android 15",
                #"refreshTokenNeeded": False,
                #"terminalMeta": "1",
                #"terminalName": "Google Pixel 7a",
                "terminalUUID": self.term_uuid
            }
        }
        response = requests.post(wap_url, json=body,verify=False)

        response.raise_for_status()
        response = response.json()
        assert response['error_code'] == 0, "Failed to login"
        self.token = response['result']['token']
        self.nickname = response['result']['nickname']
        self.account_id = response['result']['accountId']
        self.region_code = response['result']['countryCode']
        self.logged_in = True

        # Get the service urls
        body = {
            "method": "getAppServiceUrl",
            "params": {
                "serviceIds": [
                    "iot.google.prd",
                    "iot.alexa.tapo.link.prd",
                    "nbu.iot-security.appdevice",
                    "tapocare.app.nbu",
                    "nbu.iot-cloud-gateway.app",
                    "nbu.iot-app-server.app",
                    "basic.oauth-server.app.prd",
                    "nbu.iac.prd"
                ]
            }
        }
        response = requests.post(f"{wap_url}?token={self.token}", json=body, verify=False)

        response.raise_for_status()
        response = response.json()
        assert response['error_code'] == 0, "Failed to get service urls"
        self.appServiceUrl = response['result']['serviceUrls']


    def getThingsList(self):
        assert self.logged_in, "Must be logged in"
        assert self.appServiceUrl is not None, "Must have service urls"

        iot_app_server_url = self.appServiceUrl['nbu.iot-app-server.app']

        headers = {
            'app-cid': f'app:TP-Link_Tapo_Android:{self.term_uuid}',
            'authorization': f'ut|{self.token}',
            'x-term-id': self.term_uuid
        }

        response = requests.get(f"{iot_app_server_url}/v2/things", headers=headers, verify=False)

        response.raise_for_status()
        response = response.json()
        return {thing['thingName']: thing for thing in response['data']}


    def getHourlyEnergyData(self, device_id: str, start_timestamp: int, end_timestamp: int):
        # Their API only keeps the past week of data
        assert self.logged_in, "Must be logged in"
        assert self.appServiceUrl is not None, "Must have service urls"

        iot_app_server_url = self.appServiceUrl['nbu.iot-app-server.app']

        # floor the start time to the nearest day
        start_timestamp = math.floor(start_timestamp / 86400) * 86400

        # ceil the end time to the nearest day
        end_timestamp = math.ceil(end_timestamp / 86400) * 86400

        time_ranges = []
        interval = 3600*24 # 1 day
        while start_timestamp < end_timestamp:
            time_ranges.append((start_timestamp, min(start_timestamp + interval, end_timestamp)))
            start_timestamp += interval

        energy_data = []
        for start, end in time_ranges:
            body = {
                "method": "get_energy_data",
                "params": {
                    "end_timestamp": int(end),
                    "interval": 60,
                    "start_timestamp": int(start)
                }
            }
            headers = {
                'app-cid': f'app:TP-Link_Tapo_Android:{self.term_uuid}',
                'authorization': f'ut|{self.token}',
                'x-term-id': self.term_uuid
            }
            response = requests.post(f"{iot_app_server_url}/v1/things/{device_id}/usage", json=body, headers=headers, verify=False)

            response.raise_for_status()
            response = response.json()

            times = np.arange(
                response['energy_data']['start_timestamp'],
                response['energy_data']['end_timestamp'],
                response['energy_data']['interval']*60
            )

            energy_usage = np.array(response['energy_data']['data'])
            # convert to kwh
            energy_usage = energy_usage / 1000

            # Make a pandas dataframe
            df = pd.DataFrame({
                "energy_usage": energy_usage
            }, index=times)

            energy_data.append(df)

        energy_data = pd.concat(energy_data)
        # Remove duplicate timestamps. (This can happen when intervals overlap but the reading for a timestamp shouldn't change)
        energy_data = energy_data[~energy_data.index.duplicated(keep='first')]
        energy_data = energy_data.sort_index()

        return energy_data

    def getDailyEnergyData(self, device_id: str, start_timestamp: int, end_timestamp: int):
        # Their api requires that the start time is the starting day of a month and the end date will be exactly 3 months later at  the end of that month
        assert self.logged_in, "Must be logged in"
        assert self.appServiceUrl is not None, "Must have service urls"

        iot_app_server_url = self.appServiceUrl['nbu.iot-app-server.app']

        start_time = datetime.fromtimestamp(start_timestamp, tz=timezone)
        end_time = datetime.fromtimestamp(end_timestamp, tz=timezone)

        # floor the start time to the nearest month
        start_time = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        time_ranges = []
        month_interval = 3
        while start_time <= end_time:
            # Create a new end time 3 months later
            new_end_time = (start_time + timedelta(days=30*month_interval+1)).replace(day=1)
            time_ranges.append((start_time.astimezone(pytz.utc).timestamp(), new_end_time.astimezone(pytz.utc).timestamp()))
            start_time = new_end_time

        energy_data = []
        for start, end in time_ranges:
            body = {
                "method": "get_energy_data",
                "params": {
                    "end_timestamp": int(end),
                    "interval": 60*24, # 1 day
                    "start_timestamp": int(start)
                }
            }
            headers = {
                'app-cid': f'app:TP-Link_Tapo_Android:{self.term_uuid}',
                'authorization': f'ut|{self.token}',
                'x-term-id': self.term_uuid
            }
            response = requests.post(f"{iot_app_server_url}/v1/things/{device_id}/usage", json=body, headers=headers,
                                     verify=False)

            response.raise_for_status()
            response = response.json()

            times = np.arange(
                response['energy_data']['start_timestamp'],
                response['energy_data']['end_timestamp'],
                response['energy_data']['interval'] * 60
            )

            energy_usage = np.array(response['energy_data']['data'])
            # convert to kwh
            energy_usage = energy_usage / 1000

            # Make a pandas dataframe
            df = pd.DataFrame({
                "energy_usage": energy_usage
            }, index=times)

            energy_data.append(df)

        energy_data = pd.concat(energy_data)
        # Remove duplicate timestamps. (This can happen when intervals overlap but the reading for a timestamp shouldn't change)
        energy_data = energy_data[~energy_data.index.duplicated(keep='first')]
        energy_data = energy_data.sort_index()

        return energy_data
