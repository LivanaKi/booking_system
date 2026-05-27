from app.ml_recommender import recommend_slots


def test_recommendation_does_not_return_occupied_slot():
    bookings = [
        {"booking_date": "2026-05-20", "booking_time": "10:00", "specialist_id": 1, "status": "confirmed"},
        {"booking_date": "2026-05-20", "booking_time": "11:00", "specialist_id": 1, "status": "new"},
    ]
    result, _ = recommend_slots(bookings, "2026-05-20", 1)
    assert "10:00" not in result
    assert "11:00" not in result
    assert len(result) > 0
