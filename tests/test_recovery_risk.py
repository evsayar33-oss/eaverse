import unittest
import pandas as pd
from eaverse import recovery_risk as risk

class RecoveryRiskTests(unittest.TestCase):
    def test_deep_but_fast_recovery_is_eligible(self):
        dates=pd.to_datetime(["2024-01-02","2024-01-20","2024-02-01"])
        # Falls by half, then fully recovers. No drawdown magnitude gate.
        m=risk.measure([-.5,1.,.1],dates,"2024-01-01")
        self.assertEqual(m["max_recovery_days"],19)
        self.assertIsNone(risk.reason({**m,"net":.1,"drawdown":.5}))

    def test_shallow_but_slow_recovery_fails(self):
        dates=pd.to_datetime(["2024-01-02","2024-08-01","2024-08-02"])
        m=risk.measure([-.01,0.,.02],dates,"2024-01-01")
        self.assertGreater(m["max_recovery_days"],180)
        self.assertEqual(risk.reason({**m,"net":.01}),"recovery_time_exceeded")

    def test_bankruptcy_never_recovered_by_later_return(self):
        m=risk.measure([.1,-1.,100.],pd.date_range("2024-02-01",periods=3),"2024-02-01")
        self.assertEqual(risk.reason(m),"capital_ruin")

    def test_first_bar_loss_and_open_underwater_are_counted(self):
        dates=pd.to_datetime(["2024-01-01","2024-01-03","2024-07-20"])
        m=risk.measure([-.1,.2,-.01],dates,"2024-01-01")
        self.assertEqual(m["completed_recovery_days"],2)
        self.assertGreater(m["current_underwater_days"],180)
        self.assertEqual(risk.reason(m),"recovery_time_exceeded")
