from flask import Flask,request
from kiteconnect import KiteConnect
from scanner import run_scanner

API_KEY="YOUR_API_KEY"
API_SECRET="YOUR_API_SECRET"

app = Flask(__name__)

kite = KiteConnect(api_key=API_KEY)

@app.route("/login")

def login():

    request_token = request.args.get("request_token")

    data = kite.generate_session(request_token,API_SECRET)

    access_token = data["access_token"]

    kite.set_access_token(access_token)

    run_scanner(kite)

    return "Scanner started"

app.run(host="0.0.0.0",port=8080)
