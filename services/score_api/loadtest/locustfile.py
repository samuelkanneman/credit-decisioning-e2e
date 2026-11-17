"""Short load test: locust -f services/score_api/loadtest/locustfile.py --host http://localhost:8000"""

import random

from locust import HttpUser, between, task

PURPOSES = ["debt_consolidation", "credit_card", "home_improvement", "other", "medical"]


def application() -> dict:
    return {
        "acc_open_past_24mths": random.randint(0, 12),
        "fico": random.randrange(660, 850, 5),
        "mths_since_recent_inq": random.choice([None, *range(0, 24)]),
        "mo_sin_rcnt_tl": random.randint(0, 40),
        "inq_last_6mths": random.randint(0, 4),
        "dti": round(random.uniform(0, 40), 2),
        "mo_sin_old_rev_tl_op": random.randint(12, 400),
        "bc_open_to_buy": random.randint(0, 50_000),
        "purpose": random.choice(PURPOSES),
        "tot_hi_cred_lim": random.randint(5_000, 500_000),
        "loan_amnt": random.randrange(1_000, 40_000, 500),
        "annual_inc": random.randint(20_000, 200_000),
        "home_ownership": random.choice(["MORTGAGE", "RENT", "OWN"]),
        "mths_since_recent_bc": random.randint(0, 120),
        "term": random.choice([36, 60]),
    }


class Applicant(HttpUser):
    wait_time = between(0.01, 0.05)

    @task
    def score(self):
        self.client.post("/score", json=application())
