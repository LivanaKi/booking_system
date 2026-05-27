from collections import Counter, defaultdict

WORKING_SLOTS = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00"]


def recommend_slots(bookings, target_date: str, specialist_id: int, limit: int = 3):
    occupied = {
        b["booking_time"] for b in bookings
        if b["booking_date"] == target_date
        and b["specialist_id"] == specialist_id
        and b["status"] != "cancelled"
    }
    free_slots = [slot for slot in WORKING_SLOTS if slot not in occupied]
    if not free_slots:
        return [], "Усі часові слоти для обраного спеціаліста зайняті."

    positive_history = [b for b in bookings if b["status"] in ("confirmed", "completed") and b["specialist_id"] == specialist_id]
    negative_history = [b for b in bookings if b["status"] in ("cancelled", "no_show") and b["specialist_id"] == specialist_id]
    load_by_slot = Counter(b["booking_time"] for b in positive_history)
    risk_by_slot = Counter(b["booking_time"] for b in negative_history)

    scored = []
    for slot in free_slots:
        historical_load = load_by_slot[slot]
        cancellation_risk = risk_by_slot[slot]
        hour = int(slot.split(":")[0])
        comfort_penalty = 0 if hour in (10, 11, 14, 15) else 1
        score = historical_load + cancellation_risk * 2 + comfort_penalty
        scored.append((score, historical_load, cancellation_risk, hour, slot))

    scored.sort()
    recommended = [slot for *_rest, slot in scored[:limit]]
    explanation = (
        "Рекомендація сформована на основі історії записів, зайнятості спеціаліста, "
        "ризику скасувань/no-show та доступності слотів."
    )
    return recommended, explanation


def forecast_load(bookings, specialist_id: int):
    counter = Counter(
        b["booking_time"] for b in bookings
        if b["specialist_id"] == specialist_id and b["status"] in ("new", "confirmed", "completed")
    )
    return [{"slot": slot, "load": counter[slot]} for slot in WORKING_SLOTS]


def forecast_service_demand(bookings, services, horizon_days: int = 7):
    """Forecast demand for each service using historical booking counts.
    The result is deterministic and works without external ML packages.
    """
    counts = Counter(b["service_id"] for b in bookings if b["status"] in ("new", "confirmed", "completed"))
    total = sum(counts.values()) or 1
    result = []
    for service in services:
        base = counts[service["id"]]
        share = base / total
        forecast = max(1, round(base * 0.6 + share * horizon_days)) if base else 1
        result.append({
            "service_id": service["id"],
            "service_name": service["name"],
            "history_count": base,
            "forecast": forecast,
            "share": round(share * 100, 1),
        })
    return sorted(result, key=lambda x: x["forecast"], reverse=True)


def classify_client(bookings, client_id: int):
    client_bookings = [b for b in bookings if b["client_id"] == client_id]
    completed = sum(1 for b in client_bookings if b["status"] == "completed")
    confirmed = sum(1 for b in client_bookings if b["status"] == "confirmed")
    active_score = completed + confirmed
    cancelled = sum(1 for b in client_bookings if b["status"] == "cancelled")
    no_show = sum(1 for b in client_bookings if b["status"] == "no_show")

    if no_show >= 2:
        label = "ризиковий"
    elif active_score >= 5 and cancelled <= 1:
        label = "VIP"
    elif active_score >= 2:
        label = "регулярний"
    else:
        label = "новий"

    return {
        "label": label,
        "total": len(client_bookings),
        "completed": completed,
        "confirmed": confirmed,
        "cancelled": cancelled,
        "no_show": no_show,
    }


def classify_all_clients(bookings, clients):
    return [{"client_id": c["id"], "client_name": c["name"], **classify_client(bookings, c["id"])} for c in clients]


def allocate_clients_to_specialists(bookings, specialists, specialist_services, service_id: int, target_date: str):
    """Recommend specialists for a selected service by load and risk.
    Lower score means better candidate.
    """
    service_specialists = [sp for sp in specialists if service_id in specialist_services.get(sp["id"], set())]
    result = []
    for sp in service_specialists:
        daily_load = sum(1 for b in bookings if b["specialist_id"] == sp["id"] and b["booking_date"] == target_date and b["status"] != "cancelled")
        historical_load = sum(1 for b in bookings if b["specialist_id"] == sp["id"] and b["status"] in ("new", "confirmed", "completed"))
        risk = sum(1 for b in bookings if b["specialist_id"] == sp["id"] and b["status"] in ("cancelled", "no_show"))
        score = daily_load * 3 + historical_load + risk * 2
        result.append({
            "specialist_id": sp["id"],
            "specialist_name": sp["name"],
            "specialization": sp["specialization"],
            "daily_load": daily_load,
            "historical_load": historical_load,
            "risk": risk,
            "score": score,
        })
    return sorted(result, key=lambda x: x["score"])


def optimize_schedule(bookings, specialists):
    """Generate schedule optimization recommendations for each specialist.
    Uses slot load distribution and suggests focus slots and break slots.
    """
    recommendations = []
    for sp in specialists:
        slot_counter = Counter(
            b["booking_time"] for b in bookings
            if b["specialist_id"] == sp["id"] and b["status"] in ("new", "confirmed", "completed")
        )
        if slot_counter:
            peak_slot, peak_load = slot_counter.most_common(1)[0]
            free_or_low = min(WORKING_SLOTS, key=lambda slot: slot_counter[slot])
        else:
            peak_slot, peak_load, free_or_low = "10:00", 0, "12:00"
        recommendations.append({
            "specialist_id": sp["id"],
            "specialist_name": sp["name"],
            "peak_slot": peak_slot,
            "peak_load": peak_load,
            "recommended_break": free_or_low,
            "recommendation": f"Планувати основні записи біля {peak_slot}, а технічну перерву — на {free_or_low}.",
        })
    return recommendations
