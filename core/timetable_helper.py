"""
Timetable helper utilities for constructing weekly schedule matrices
with periods, times, course color themes, and day alignments.
"""
from django.utils import timezone

COURSE_THEMES = {
    "CS401": "theme-blue",
    "CS402": "theme-green",
    "CS403": "theme-purple",
    "CS404": "theme-amber",
    "CS405": "theme-teal",
}

PERIOD_CONFIG = [
    {"period": 1, "start": "09:00", "end": "09:50", "time": "09:00 – 09:50"},
    {"period": 2, "start": "10:00", "end": "10:50", "time": "10:00 – 10:50"},
    {"period": 3, "start": "11:00", "end": "11:50", "time": "11:00 – 11:50"},
    {"period": 4, "start": "12:00", "end": "12:50", "time": "12:00 – 12:50"},
    {"period": "break", "is_break": True, "label": "Lunch Break", "time": "12:50 – 14:00"},
    {"period": 5, "start": "14:00", "end": "14:50", "time": "14:00 – 14:50"},
]

DAYS_CONFIG = [
    {"day": 0, "name": "Monday", "short": "Mon"},
    {"day": 1, "name": "Tuesday", "short": "Tue"},
    {"day": 2, "name": "Wednesday", "short": "Wed"},
    {"day": 3, "name": "Thursday", "short": "Thu"},
    {"day": 4, "name": "Friday", "short": "Fri"},
]


def get_course_theme(course_code):
    return COURSE_THEMES.get(course_code, "theme-blue")


def build_timetable_grid(slots_queryset, today=None):
    """
    Builds a weekly matrix grid from a TimetableSlot queryset.

    Returns:
      days: list of dicts with day info and is_today flag.
      grid_rows: list of rows (one per period/break) containing day cells with slot cards.
    """
    if today is None:
        today = timezone.localdate()
    today_weekday = today.weekday()

    days = [
        {
            "day": d["day"],
            "name": d["name"],
            "short": d["short"],
            "is_today": d["day"] == today_weekday,
        }
        for d in DAYS_CONFIG
    ]

    # Group slots by (day, period_number)
    slots_map = {}
    for slot in slots_queryset:
        theme = get_course_theme(slot.section.course.code)
        setattr(slot, "theme_class", theme)
        key = (slot.day, slot.period_number)
        slots_map.setdefault(key, []).append(slot)

    grid_rows = []
    for item in PERIOD_CONFIG:
        if item.get("is_break"):
            grid_rows.append({
                "is_break": True,
                "label": item["label"],
                "time": item["time"],
            })
        else:
            p_num = item["period"]
            day_cells = []
            for d in days:
                d_idx = d["day"]
                matching_slots = slots_map.get((d_idx, p_num), [])
                day_cells.append({
                    "day": d_idx,
                    "period": p_num,
                    "is_today": d["is_today"],
                    "slots": matching_slots,
                })

            grid_rows.append({
                "is_break": False,
                "period": p_num,
                "time": item["time"],
                "cells": day_cells,
            })

    return {
        "days": days,
        "grid_rows": grid_rows,
        "today_weekday": today_weekday,
        "today": today,
    }
