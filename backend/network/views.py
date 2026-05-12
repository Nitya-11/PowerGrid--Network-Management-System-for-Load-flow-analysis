"""
network/views.py
API views for GridPulse dashboard.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.utils import timezone
from django.utils.dateparse import parse_datetime

import pandas as pd

from .models import NetworkBus, SmartMeterLoad, VoltageResult
from .serializers import (
    NetworkBusSerializer,
    SmartMeterLoadSerializer,
    DashboardSummarySerializer
)

from .pandapower_sim import run_full_day_simulation


# ─────────────────────────────────────────────────────────────
# BUS LIST API
# GET /api/buses/
# ─────────────────────────────────────────────────────────────
class BusListView(APIView):

    def get(self, request):

        buses = NetworkBus.objects.all()

        serializer = NetworkBusSerializer(
            buses,
            many=True
        )

        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
# LOAD DATA API
# GET /api/loads/?date=2026-01-01
# ─────────────────────────────────────────────────────────────
class SmartMeterLoadView(APIView):

    def get(self, request):

        date_str = request.query_params.get('date', None)

        if date_str:
            loads = SmartMeterLoad.objects.filter(
                timestamp__date=date_str
            )
        else:
            loads = SmartMeterLoad.objects.all()

        serializer = SmartMeterLoadSerializer(
            loads,
            many=True
        )

        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
# SIMULATION DATA API
# GET /api/simulation/?date=2026-01-01
# ─────────────────────────────────────────────────────────────
class SimulationDataView(APIView):

    def get(self, request):

        date_str = request.query_params.get(
            'date',
            '2026-01-01'
        )

        # Fetch existing simulation results
        results = VoltageResult.objects.filter(
            timestamp__date=date_str
        ).select_related('bus').order_by(
            'timestamp',
            'bus__bus_id'
        )

        # ── AUTO RUN SIMULATION IF DATA DOES NOT EXIST ──
        if not results.exists():

            # Load smart meter load data
            loads_qs = SmartMeterLoad.objects.filter(
                timestamp__date=date_str
            ).values(
                'timestamp',
                'bus_id',
                'p_kw',
                'q_kvar'
            )

            # No load data available
            if not loads_qs.exists():

                return Response(
                    {
                        'error': f'No load data found for {date_str}'
                    },
                    status=status.HTTP_404_NOT_FOUND
                )

            # Convert queryset to DataFrame
            loads_df = pd.DataFrame(list(loads_qs))

            print(f"Running automatic simulation for {date_str}...")

            # Run pandapower simulation
            results_df = run_full_day_simulation(loads_df)

            # Delete old results if any
            VoltageResult.objects.filter(
                timestamp__date=date_str
            ).delete()

            # Prepare DB objects
            voltage_objects = []

            for _, row in results_df.iterrows():

                try:

                    bus = NetworkBus.objects.get(
                        bus_id=int(row['bus_id'])
                    )

                    voltage_objects.append(
                        VoltageResult(
                            bus=bus,
                            timestamp=row['timestamp'],
                            vm_pu=row['vm_pu'],
                            status=row['status']
                        )
                    )

                except NetworkBus.DoesNotExist:
                    continue

            # Bulk insert
            VoltageResult.objects.bulk_create(
                voltage_objects
            )

            # Reload results
            results = VoltageResult.objects.filter(
                timestamp__date=date_str
            ).select_related('bus').order_by(
                'timestamp',
                'bus__bus_id'
            )

        # ─────────────────────────────────────────────
        # Format response for React frontend
        # ─────────────────────────────────────────────
        buses_dict = {}
        timestamps_set = []

        for r in results:

            ts_label = r.timestamp.strftime('%H:%M')

            if ts_label not in timestamps_set:
                timestamps_set.append(ts_label)

            if r.bus.bus_id not in buses_dict:

                buses_dict[r.bus.bus_id] = {
                    'bus_id': r.bus.bus_id,
                    'bus_name': r.bus.bus_name,
                    'kv': r.bus.kv,
                    'zone': r.bus.zone,
                    'bus_type': r.bus.bus_type,
                    'voltages': [],
                    'statuses': []
                }

            buses_dict[r.bus.bus_id]['voltages'].append(
                r.vm_pu
            )

            buses_dict[r.bus.bus_id]['statuses'].append(
                r.status
            )

        return Response({
            'date': date_str,
            'timestamps': timestamps_set,
            'buses': list(buses_dict.values())
        })


# ─────────────────────────────────────────────────────────────
# DASHBOARD SUMMARY API
# GET /api/dashboard-summary/?timestamp=2026-01-01T12:00:00
# ─────────────────────────────────────────────────────────────
class DashboardSummaryView(APIView):

    def get(self, request):

        ts_str = request.query_params.get(
            'timestamp',
            None
        )

        if ts_str:

            ts = parse_datetime(ts_str)

            if ts is None:

                return Response(
                    {'error': 'Invalid timestamp format'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if timezone.is_naive(ts):

                ts = timezone.make_aware(
                    ts,
                    timezone.get_current_timezone()
                )

            results = VoltageResult.objects.filter(
                timestamp=ts
            )

        else:

            latest_ts = VoltageResult.objects.order_by(
                '-timestamp'
            ).values_list(
                'timestamp',
                flat=True
            ).first()

            if not latest_ts:

                return Response(
                    {'error': 'No voltage data found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            results = VoltageResult.objects.filter(
                timestamp=latest_ts
            )

        if not results.exists():

            return Response(
                {'error': 'No data for this timestamp'},
                status=status.HTTP_404_NOT_FOUND
            )

        voltages = [r.vm_pu for r in results]
        statuses = [r.status for r in results]

        summary = {

            'avg_voltage': round(
                sum(voltages) / len(voltages),
                3
            ),

            'min_voltage': round(
                min(voltages),
                3
            ),

            'max_voltage': round(
                max(voltages),
                3
            ),

            'healthy_count': statuses.count('NORMAL'),

            'warning_count': statuses.count('WARNING'),

            'critical_count': statuses.count('CRITICAL'),

            'total_buses': len(voltages),

            'snapshot_time': results.first().timestamp.strftime(
                '%H:%M'
            )
        }

        serializer = DashboardSummarySerializer(
            summary
        )

        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
# MANUAL RUN SIMULATION API
# POST /api/run-simulation/?date=2026-01-01
# ─────────────────────────────────────────────────────────────
class RunSimulationView(APIView):

    def post(self, request):

        date_str = request.query_params.get(
            'date',
            '2026-01-01'
        )

        loads_qs = SmartMeterLoad.objects.filter(
            timestamp__date=date_str
        ).values(
            'timestamp',
            'bus_id',
            'p_kw',
            'q_kvar'
        )
        
        print(loads_qs.count())
        print(loads_qs.first())

        if not loads_qs.exists():

            return Response(
                {
                    'error': f'No load data for {date_str}'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        loads_df = pd.DataFrame(
            list(loads_qs)
        )

        print(
            f"Running simulation for {date_str}..."
        )

        # Run simulation
        results_df = run_full_day_simulation(
            loads_df
        )

        # Delete old results
        VoltageResult.objects.filter(
            timestamp__date=date_str
        ).delete()

        # Save new results
        voltage_objects = []

        for _, row in results_df.iterrows():

            try:

                bus = NetworkBus.objects.get(
                    bus_id=int(row['bus_id'])
                )

                voltage_objects.append(
                    VoltageResult(
                        bus=bus,
                        timestamp=row['timestamp'],
                        vm_pu=row['vm_pu'],
                        status=row['status']
                    )
                )

            except NetworkBus.DoesNotExist:
                continue

        VoltageResult.objects.bulk_create(
            voltage_objects
        )

        return Response({

            'message': f'Simulation complete for {date_str}',

            'timesteps_processed': len(
                results_df['timestamp'].unique()
            ),

            'results_saved': len(
                voltage_objects
            )
        })
        
        
        

