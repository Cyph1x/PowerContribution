import time
import threading
from requests_oauthlib import OAuth2Session
import random
import base64
import bs4
import json
import pandas as pd
import logging

#existing request:
#https://login.ovoenergy.com.au/authorize

#response_type: code
#client_id: 5JHnPn71qgV3LmF3I3xX0KvfRBdROVhR
#scope: openid profile email offline_access
#redirect_uri: https://my.ovoenergy.com.au?login=oea
#code_challenge
#code_challenge_method: S256
#audience: https://login.ovoenergy.com.au/api
#nonce

class Ovo:

    def __init__(self):
        self.client_id = '5JHnPn71qgV3LmF3I3xX0KvfRBdROVhR'
        self.authorization_base_url = 'https://login.ovoenergy.com.au/authorize'
        self.token_url = 'https://login.ovoenergy.com.au/oauth/token'
        self.scope = ['openid', 'profile', 'email', 'offline_access']
        self.redirect_uri = 'https://my.ovoenergy.com.au?login=oea'
        self.audience = 'https://login.ovoenergy.com.au/api'
        self.is_logged_in = False

    def login(self, username, password):
        # Generate a random string for the state parameter
        nonce = ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRTUVWXYZ_-~', k=43))

        # ovo requires a base64 encoded nonce
        nonce = base64.urlsafe_b64encode(nonce.encode()).decode()

        # PKCE
        session = OAuth2Session(self.client_id, scope=self.scope, redirect_uri=self.redirect_uri, pkce='S256')
        authorization_url, state = session.authorization_url(self.authorization_base_url,
                                                             audience=self.audience, nonce=nonce)

        login_page = session.get(authorization_url)

        # Get the state from the url parameters
        state = login_page.url.split('state=')[1].split('&')[0]

        # Pull a base64 encoded json object from the page (most likely the longest string) (I hate this)
        longest_string = ''
        soup = bs4.BeautifulSoup(login_page.text, 'html.parser')
        scripts = soup.find_all('script')
        for script in scripts:
            script = script.text
            strings = script.split('"')
            for string in strings:
                if len(string) > len(longest_string):
                    longest_string = string

        # Parse the json object
        decoded = base64.urlsafe_b64decode(longest_string)
        json_object = json.loads(decoded)

        # Get the _csrf value
        csrf = json_object['extraParams']['_csrf']
        intstate = json_object['extraParams']['_intstate']

        # Login
        login_url = 'https://login.ovoenergy.com.au/usernamepassword/login'
        login_data = {
            'audience': 'https://login.ovoenergy.com.au/api',
            'client_id': session.client_id,
            'connection': 'prod-myovo-auth',
            'nonce': nonce,
            'password': password,
            'redirect_uri': session.redirect_uri,
            'scope': ' '.join(session.scope),
            'state': state,
            'tenant': 'ovoenergyau',
            'username': username,
            '_csrf': csrf,
            '_intstate': intstate
        }

        response = session.post(login_url, data=login_data)
        #print(response.url)
        #print(response.text)

        assert response.status_code == 200, 'Failed to login'

        soup = bs4.BeautifulSoup(response.text, 'html.parser')
        # Find the form
        form = soup.find('form')

        # Get all the inputs
        inputs = form.find_all('input')
        data = {}
        for input in inputs:
            if 'name' in input.attrs:
                data[input['name']] = input['value']

        # Submit the form
        action = form['action']
        origin = 'https://login.ovoenergy.com.au'
        referer = response.url
        response = session.post(action, data=data, headers={'Origin': origin, 'Referer': referer})

        #print(response.url)

        assert response.status_code == 200, 'Failed to login'

        # We now have the session token
        token = session.fetch_token(self.token_url, authorization_response=response.url,  include_client_id=True)

        ovo_session = OAuth2Session(self.client_id,
                                    token=token,
                                    auto_refresh_url=self.token_url,
                                    auto_refresh_kwargs={'client_id': self.client_id},
                                    token_updater=lambda token: token)

        self.is_logged_in = True
        self.session = ovo_session
        self.token = token


    def graph_ql_query(self, query):
        if not self.is_logged_in:
            raise Exception("Must be logged in")
        graphql = 'https://my.ovoenergy.com.au/graphql'
        response = self.session.post(graphql, json=query, headers={'Authorization': self.session.token['access_token'],'myovo-id-token': self.session.token['id_token']})
        return response.json()

    def get_hourly_data(self, account_id):
        if not self.is_logged_in:
            raise Exception("Must be logged in")
        graphql = 'https://my.ovoenergy.com.au/graphql'
        query = {
            "operationName": "GetUsageDownloadUrl",
            "variables": {
                "input": {
                    "id": account_id,
                }
            },
            "query":
                """
                query GetUsageDownloadUrl($input: GetAccountInfoInput!) {
                    GetAccountInfo(input: $input) {
                        usage {
                            usageDownloadUrl(input: $input)
                        }
                    }
                }
                """
        }

        response = self.session.post(graphql, json=query, headers={'Authorization': self.session.token['access_token'],'myovo-id-token': self.session.token['id_token']})
        if response.status_code != 200:
            raise Exception(f"Failed to get usage data [{response.status_code}]: {response.text}")

        # Extract the hourly data
        data_url = response.json()['data']['GetAccountInfo']['usage']['usageDownloadUrl']

        response = self.session.get(data_url)
        if response.status_code != 200:
            raise Exception(f"Failed to get usage data [{response.status_code}]: {response.text}")

        with open(f'hourly_{account_id}.csv', 'wb') as f:
            f.write(response.content)

        df = pd.read_csv(f'hourly_{account_id}.csv')

        # Confirm columns exist
        required = ['Register','ReadConsumption', 'ReadUnit', 'ReadDate', 'ReadTime']
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise Exception(f"Missing columns in data: {missing}")

        # normalize the read units TODO

        # combine date and time and convert to epoch
        df["time"] = pd.to_datetime(df['ReadDate'] + ' ' + df['ReadTime'])

        # sort by time
        df = df.sort_values('time')

        # adjust for timezone
        df["time"] = df["time"] - pd.Timedelta(hours=10)

        df["time"] = df["time"].astype(int) // 10 ** 9

        # floor to the nearest hour
        df['time'] = df['time'] // 3600 * 3600

        # unique times
        unique_times = df['time'].unique()

        out_df = pd.DataFrame({
            'time': unique_times
        }, index=unique_times)
        unique_registers = df['Register'].unique()

        # for each register type, group by time and sum the usage
        for register in unique_registers:
            register_df = df[df['Register'] == register]
            register_df = register_df.groupby('time')
            out_df[register] = register_df['ReadConsumption'].sum().values

        return out_df
