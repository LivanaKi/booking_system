import json
from app.booking import Booking

class Scheduler:
    def __init__(self, filename="data.json"):
        self.filename = filename
        self.bookings = []
        self.load()

    def load(self):
        try:
            with open(self.filename, "r") as f:
                data = json.load(f)
                self.bookings = [Booking(**b) for b in data]
        except:
            self.bookings = []

    def save(self):
        with open(self.filename, "w") as f:
            json.dump([b.__dict__ for b in self.bookings], f, indent=4)

    def add_booking(self, client_name, service, time):
        for b in self.bookings:
            if b.time == time:
                return "Time slot already booked!"
        self.bookings.append(Booking(client_name, service, time))
        self.save()
        return "Booking added!"

    def delete_booking(self, client_name):
        for b in self.bookings:
            if b.client_name == client_name:
                self.bookings.remove(b)
                self.save()
                return "Booking deleted!"
        return "Booking not found!"

    def find_booking(self, client_name):
        result = []
        for b in self.bookings:
            if client_name.lower() in b.client_name.lower():
                result.append(b)
        return result

    def get_all(self):
        return self.bookings