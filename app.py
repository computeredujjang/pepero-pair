from flask import Flask
app = Flask(__name__)

@app.route("/")
def home():
    return "PEPERO PAIR 11:11 - server connected!"
