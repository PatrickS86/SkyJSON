import json, math, os, sqlite3, time
from flask import Flask, render_template, request

app = Flask(__name__)

DB = "config.db"

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def safe_float(v):
    try: return float(v)
    except: return None

def get_source_settings():
    return {
        "source_type": "file",
        "aircraft_path": "",
        "aircraft_url": "",
        "receiver_path": "",
        "receiver_url": ""
    }

def read_payload_from_url(url):
    import requests
    try:
        return requests.get(url, timeout=10).json()
    except:
        return {"error": True}

def read_payload_from_file(path):
    try:
        return json.load(open(path))
    except:
        return {"error": True}

def read_receiver():
    s = get_source_settings()
    if s["receiver_url"]:
        p = read_payload_from_url(s["receiver_url"])
    elif s["receiver_path"]:
        p = read_payload_from_file(s["receiver_path"])
    else:
        return {"lat": None, "lon": None}
    if p.get("error"): return {"lat": None, "lon": None}
    return {"lat": p.get("lat"), "lon": p.get("lon")}

def haversine(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2): return None
    r=6371
    p1=math.radians(lat1); p2=math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return round(2*r*math.atan2(math.sqrt(a),math.sqrt(1-a)),1)

def load_aircraft():
    s = get_source_settings()
    payload = read_payload_from_file(s["aircraft_path"]) if s["source_type"]=="file" else read_payload_from_url(s["aircraft_url"])
    receiver = read_receiver()
    ac=[]
    for i in payload.get("aircraft", []):
        lat=safe_float(i.get("lat"))
        lon=safe_float(i.get("lon"))
        ac.append({
            "hex": i.get("hex"),
            "flight": i.get("flight"),
            "registration": i.get("r"),
            "lat": lat,
            "lon": lon,
            "dist_km": haversine(receiver["lat"], receiver["lon"], lat, lon)
        })
    return {
        "aircraft": ac,
        "receiver": receiver,
        "polar_points": []  # 🔥 voorkomt 500
    }

@app.route("/")
def index():
    data = load_aircraft()
    return render_template("dashboard.html",
        aircraft=data["aircraft"],
        map_aircraft=data["aircraft"],
        polar_points=data.get("polar_points", []),
        receiver=data.get("receiver", {"lat":None,"lon":None}),
        stats={},
        error=None,
        query="",
        refresh_interval=1,
        total_results=len(data["aircraft"])
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
