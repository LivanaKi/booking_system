from flask import Flask, render_template, request, redirect
from scheduler import Scheduler

app = Flask(__name__)
scheduler = Scheduler()

@app.route("/")
def index():
    bookings = scheduler.get_all()
    return render_template("index.html", bookings=bookings)

@app.route("/add", methods=["POST"])
def add():
    name = request.form["client_name"]
    service = request.form["service"]
    time = request.form["time"]
    scheduler.add_booking(name, service, time)
    return redirect("/")

@app.route("/delete/<name>")
def delete(name):
    scheduler.delete_booking(name)
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)