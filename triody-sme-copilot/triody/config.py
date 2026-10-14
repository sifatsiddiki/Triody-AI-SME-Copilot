"""Settings in one place. Change values here, not inside the modules."""
from datetime import date

# Demo shop and forecasting
AS_OF = date(2026, 10, 14)        # last day of history in the demo shop
HISTORY_DAYS = 180
SEED = 20261014                   # every random draw in the project is seeded
HORIZON_DAYS = 60
SIMULATIONS = 2000
ALERT_BUFFER_GBP = 2500.0         # minimum balance the owner wants to keep
WARNING_PROBABILITY = 0.25        # warn when P(balance < buffer) reaches this level

# Permission controls
MAX_APPLICATION_GBP = 50_000.0
MAX_EXECUTED_ACTIONS_PER_DAY = 3
CONFIRMATION_TTL_SECONDS = 600
