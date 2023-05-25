from multiprocessing.sharedctypes import Value
from ogn.client import AprsClient, settings
from ogn.parser import parse, ParseError
from flask import Flask
import time
import datetime
from flask import request
from waitress import serve
import threading
from telegrambot import TelegramBot
import pandas as pd


class beacon_class:
    
    def __init__(self, address, name, latitude, longitude, track, altitude, ground_speed, climb_rate, reference_timestamp, beacon_type):
        self.address = address
        self.name = name[-4:]
        self.latitude = latitude
        self.longitude = longitude
        self.track = track
        self.altitude = altitude
        self.ground_speed = ground_speed
        self.climb_rate = climb_rate
        self.reference_timestamp = reference_timestamp
        self.beacon_type = beacon_type

    def __eq__(self, other):
        return self.address == other.address


current_messages = []
timestamp = time.time()


# bounds: array of strings lat, long
def filter_messages(bounds):
    global current_messages
    
    filtered_message_str = ""
    counter = 0
    #print("filtering")
    average = []
    try:
        average.append((float(bounds[0]) + float(bounds[1])) / 2)
        average.append((float(bounds[2]) + float(bounds[3])) / 2)
    except ValueError as err:
        return "invalid bound values"
    names_df = pd.read_csv("names.csv", names=["fid","name"], header=0)
    #print(names_df)
    for msg in current_messages:
        if abs(float(msg.latitude) - average[0]) < 0.5:
            #print("in lat")
            if abs(float(msg.longitude) - average[1]) < 0.5:
                #print(msg.name + " short " + msg.name[-4:])
                all_nicknames = names_df[names_df["fid"] == msg.name]
                #print("ndf" + names_df["fid"].iloc[1] + "msg" + msg.name)
                #print(names_df["fid"].isin([msg.name]))
                nickname = msg.name
                if (len(all_nicknames) > 0):
                    nickname = all_nicknames["name"].iloc[0]
                    if (nickname == '....'):
                        continue
                
                #print(msg.beacon_type)

                filtered_message_str += f'{nickname},{str(msg.latitude)[:8]},{str(msg.longitude)[:8]},{msg.track},{str(round(msg.altitude))},{round(msg.ground_speed)},{round(msg.climb_rate, 1)},{round(msg.reference_timestamp.timestamp())},{msg.beacon_type}\n'
                counter += 1
    filtered_message_str = f"{str(counter)},{str(counter)}\n{filtered_message_str}"
    return filtered_message_str

def process_beacon(raw_message):
    global current_messages
    global timestamp
    #print(time.time())
    #print(time.time() + 30)
    if time.time() > timestamp + 30:
        # delete
        i = 0
        timestamp = time.time()
        while i < len(current_messages):
            
            if (datetime.datetime.utcnow() - current_messages[i].reference_timestamp).total_seconds() > 30:
                print("too old: " + str(current_messages[i]))
                current_messages.pop(i)
                i -= 1
            i += 1
        print(len(current_messages))
    try:
        
        beacon = parse(raw_message)
        #print('Received {aprs_type}: {raw_message}'.format(**beacon))
        #print(current_messages)
        #print(beacon)
        if "address" in beacon:
            names_df = pd.read_csv("names.csv", names=["fid","name"], header=0)
            dt = datetime.datetime.now()
            if beacon["address"] in names_df["fid"].values:
                nickname = names_df[names_df["fid"].str.contains(beacon["address"][2:])].iloc[0]["name"]
                igcFile = open(str(dt.year) + str(dt.month) + str(dt.day) + nickname + ".igc", "a")
                lat_d = beacon["latitude"]
                lat_m = (lat_d - int(lat_d))*60
                lat_s = (lat_m - int(lat_m))*60
                long_d = beacon["longitude"]
                long_m = (long_d - int(long_d))*60
                long_s = (long_m - int(long_m))*60
                igcFile.write(f'B{beacon["reference_timestamp"].hour}{beacon["reference_timestamp"].minute}{beacon["reference_timestamp"].second}{int(lat_d)}{int(lat_m)}{int(lat_s*10)}N{int(long_d)}{int(long_m)}{int(long_s*10)}EA000000{int(beacon["altitude"]*10)}\n')
                igcFile.close()
        if("address" in beacon and "ground_speed" in beacon and "climb_rate" in beacon and "symbolcode" in beacon and beacon["symbolcode"] is not 'n' and beacon["symbolcode"] is not 'X' and beacon["symbolcode"] is not '^' and beacon["symbolcode"] is not 'g'):
            #print(beacon["symbolcode"])
            target_lat = float(serverdata[2].strip())
            target_lon = float(serverdata[3].strip())
            if beacon["latitude"] < target_lat + 0.01 and beacon["latitude"] > target_lat - 0.01 and beacon["longitude"] < target_lon + 0.01 and beacon["longitude"] > target_lon - 0.01:
                loc_file = open("location.txt", "a")
                loc_file.write(f'{beacon["address"]},{beacon["latitude"]},{beacon["longitude"]},{beacon["track"]},{beacon["altitude"]},{beacon["ground_speed"]},{beacon["climb_rate"]},{beacon["reference_timestamp"]},{beacon["symbolcode"]}')
                loc_file.close()
            current_beacon = beacon_class(beacon["address"], beacon["name"], beacon["latitude"], beacon["longitude"], beacon["track"], beacon["altitude"], beacon["ground_speed"], beacon["climb_rate"], beacon["reference_timestamp"], beacon["symbolcode"])
            try:
                ind = current_messages.index(current_beacon)
                current_messages[ind] = current_beacon
            except ValueError as ve:
                current_messages.append(current_beacon)
            #f'{beacon["address"]},{beacon["name"]},{beacon["name"]},{beacon["latitude"]},{beacon["longitude"]},{beacon["track"]},{beacon["altitude"]},{beacon["ground_speed"]},{beacon["climb_rate"]},{beacon["reference_timestamp"]},{beacon["beacon_type"]}')
    except ParseError as e:
        print('Error, {}'.format(e.message))
    except NotImplementedError as e:
        print('{}: {}'.format(e, raw_message))
    except AttributeError as e:
        print('{}: {}'.format(e, raw_message))

def create_ogn_client():
    settings.APRS_SERVER_HOST = "glidern3.glidernet.org"
    client = AprsClient(aprs_user='N0CALL', aprs_filter="", settings=settings)
    client.connect()
    client.run(callback=process_beacon, autoreconnect=True)

tbot = TelegramBot()
threading.Thread(target = create_ogn_client, args=()).start()
#threading.Thread(target = tbot.create_telegram_bot, args=()).start()
#client.run(callback=process_beacon, autoreconnect=False)

serverdata_file = open("serverdata.txt", "r")
serverdata = serverdata_file.readlines()
serverdata_file.close()

app = Flask("asdf")

@app.route("/")
def get_all():
    
    token = request.args.get('access_token')
    if token is not None:
        if(token == serverdata[0].strip()):
            bounds = request.args.get('bounds')
            if bounds is not None:
                bounds_array = bounds.split(",")
                if len(bounds_array) == 4:
                    return filter_messages(bounds_array)
    
    return ""

#app.run(, debug=True)
def serve_app():
    serve(app, host=serverdata[1].strip(), port=8000)

threading.Thread(target=serve_app).start()
tbot.create_telegram_bot()