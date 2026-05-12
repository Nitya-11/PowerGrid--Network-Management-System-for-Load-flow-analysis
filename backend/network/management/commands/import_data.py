"""
network/management/commands/import_data.py

1. Import buses from CSV (network_buses.csv)
2. Generate 24-hour smart meter data (15-min interval)
3. Store everything in PostgreSQL

Run:
    python manage.py import_data
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from network.models import NetworkBus, SmartMeterLoad

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = "Import buses from CSV + generate full-day smart meter data"

    def handle(self, *args, **kwargs):

        # ─────────────────────────────
        # STEP 0: Clear old data
        # ─────────────────────────────
        self.stdout.write("🔄 Clearing old data...")
        SmartMeterLoad.objects.all().delete()
        NetworkBus.objects.all().delete()

        # ─────────────────────────────
        # STEP 1: LOAD BUS CSV
        # ─────────────────────────────
        self.stdout.write("📂 Loading network_buses.csv...")

        df_bus = pd.read_csv("network_buses.csv")

        bus_objects = []
        for _, row in df_bus.iterrows():
            bus_objects.append(NetworkBus(
                bus_id=row['bus_id'],
                bus_name=row['bus_name'],
                kv=row['kv'],
                zone=row['zone'],
                bus_type=row['bus_type']
            ))

        NetworkBus.objects.bulk_create(bus_objects)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Imported {len(bus_objects)} buses"
        ))

        # ─────────────────────────────
        # STEP 2: GENERATE 24H DATA
        # ─────────────────────────────
        self.stdout.write("📊 Generating 24h smart meter data...")

        base_date = datetime(2026, 1, 1)
        tz = timezone.get_current_timezone()

        # 96 points (24h × 15min)
        timestamps = [
            timezone.make_aware(base_date + timedelta(minutes=15 * i), tz)
            for i in range(96)
        ]

        np.random.seed(42)

        # Base load for each bus (you can tweak)
        base_loads = {
            1: (120, 40),
            2: (100, 35),
            3: (90, 30),
            4: (80, 25),
            5: (110, 38),
            6: (105, 36),
            7: (70, 20),
        }

        bus_peak_factor = {
            1: 1.50,
            2: 1.35,
            3: 1.20,
            4: 1.80,
            5: 1.65,
            6: 1.45,
            7: 1.10,
        }

        def generate_profile(base_p, base_q, timestamps, peak_factor=1.0, bus_id=None):
            data = []
            
            # Different patterns for different bus types/zones
            if bus_id in [1, 4]:  # Substations - more industrial/commercial pattern
                pattern_type = 'commercial'
            elif bus_id in [2, 3, 5, 6]:  # Feeders - residential/mixed
                pattern_type = 'residential'  
            else:  # Bus 7 - small distribution
                pattern_type = 'small'

            for ts in timestamps:
                hour = ts.hour + ts.minute / 60

                # Base daily curve with different patterns per bus type
                if pattern_type == 'commercial':
                    # Commercial: high during business hours, moderate evenings
                    if 0 <= hour < 6:
                        mult = 0.3
                    elif 6 <= hour < 9:
                        mult = 0.8 + 0.2 * (hour - 6) / 3
                    elif 9 <= hour < 17:
                        mult = 1.2 + 0.1 * np.sin((hour - 9) / 8 * np.pi)
                    elif 17 <= hour < 20:
                        mult = 1.1 - 0.2 * (hour - 17) / 3
                    else:
                        mult = 0.4
                elif pattern_type == 'residential':
                    # Residential: low mornings, high evenings
                    if 0 <= hour < 5:
                        mult = 0.35
                    elif 5 <= hour < 9:
                        mult = 0.6 + 0.15 * (hour - 5) / 4
                    elif 9 <= hour < 17:
                        mult = 0.75 - 0.1 * (hour - 9) / 8
                    elif 17 <= hour < 21:
                        mult = 0.9 + 0.3 * np.sin((hour - 17) / 4 * np.pi)
                    else:
                        mult = 0.5
                else:  # small
                    # Small loads: more random, less predictable
                    mult = 0.6 + 0.3 * np.sin(hour / 24 * 2 * np.pi) + 0.2 * np.cos(hour / 12 * 2 * np.pi)

                # Add more realistic noise and random events
                base_noise = np.random.normal(scale=0.15)  # Increased noise
                
                # Random demand spikes (5% chance per 15min interval)
                spike = 0
                if np.random.random() < 0.05:
                    spike = np.random.uniform(0.2, 0.8)  # Random spike magnitude
                
                # Occasional drops (3% chance)
                drop = 0
                if np.random.random() < 0.03:
                    drop = -np.random.uniform(0.1, 0.4)
                
                total_mult = mult + base_noise + spike + drop
                p = base_p * max(0.1, total_mult) * peak_factor
                q = base_q * max(0.1, total_mult + np.random.normal(scale=0.1)) * peak_factor

                data.append((round(p, 1), round(q, 1)))

            return data

        # ─────────────────────────────
        # STEP 3: SAVE LOAD DATA
        # ─────────────────────────────
        load_objects = []

        for bus_id, (base_p, base_q) in base_loads.items():
            try:
                bus = NetworkBus.objects.get(bus_id=bus_id)
            except NetworkBus.DoesNotExist:
                continue

            peak_factor = bus_peak_factor.get(bus_id, 1.0)
            profile = generate_profile(base_p, base_q, timestamps, peak_factor=peak_factor, bus_id=bus_id)

            for ts, (p, q) in zip(timestamps, profile):
                load_objects.append(SmartMeterLoad(
                    bus=bus,
                    timestamp=ts,
                    p_kw=p,
                    q_kvar=q
                ))

        SmartMeterLoad.objects.bulk_create(load_objects)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Created {len(load_objects)} load records"
        ))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("🎉 DONE! Data ready for dashboard"))