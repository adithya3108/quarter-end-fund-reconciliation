import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACK = ROOT / "pack"


@pytest.fixture(scope="session")
def pack():
    if not (PACK / "GPF_Bank_Statement_Q4_2022.pdf").exists():
        pytest.skip("pack/ not present")
    return PACK


@pytest.fixture(scope="session")
def statement(pack):
    from agents.bank_agent.statement_parser import parse_statement
    return parse_statement(str(pack / "GPF_Bank_Statement_Q4_2022.pdf"))


@pytest.fixture(scope="session")
def q3(pack):
    from agents.valuation_agent.data import load_q3_position
    return load_q3_position(str(pack / "GPF_NAV_Summary_Q3_2022.xlsx"), str(pack / "CVP_Valuation_Report_Q3_2022.xlsx"))


@pytest.fixture(scope="session")
def agent_b_data(pack):
    from agents.valuation_agent.data import load_agent_b_data
    return load_agent_b_data(str(pack / "agent_b_data.json"))
