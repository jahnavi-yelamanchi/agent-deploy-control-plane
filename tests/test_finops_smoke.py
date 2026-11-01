from pathlib import Path

import yaml

from adcp.finops.report import INFRA_PRICES, build, node_pools, parse_tf_variables

ROOT = Path(__file__).resolve().parents[1]


def test_tf_parse_and_cluster_table(tmp_path):
    v = parse_tf_variables(ROOT / "terraform" / "aws")
    assert v["cpu_node_pool"]["instance_type"]
    prices = yaml.safe_load(INFRA_PRICES.read_text())
    pools = node_pools("azure", prices)
    assert pools[0].hourly_usd > 0
    md = build(ROOT / "specs", tmp_path / "finops.md", skip_runs=True)
    assert "EKS control plane" in md and "AKS" in md and (tmp_path / "finops.md").exists()
