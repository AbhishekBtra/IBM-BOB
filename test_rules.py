"""Inline validation: run all 8 rules against known-trigger SQL snippets."""
import sys
sys.path.insert(0, ".")

from analyzer.rules import (
    check_select_star, check_null_handling, check_implicit_cast,
    check_missing_dedup, check_unfiltered_scan, check_cross_join,
    check_hardcoded_literals, check_idempotency, ALL_RULES,
)

cases = []

# DQ-001
cases.append(("DQ-001 fires on SELECT *", check_select_star("SELECT * FROM t"), "DQ-001"))
cases.append(("DQ-001 silent on COUNT(*)", check_select_star("SELECT COUNT(*) FROM t"), None))
cases.append(("DQ-001 silent on explicit cols", check_select_star("SELECT id, name FROM t"), None))

# DQ-002
cases.append(("DQ-002 fires bare LEFT JOIN", check_null_handling("SELECT a.x FROM a LEFT JOIN b ON a.id=b.id"), "DQ-002"))
cases.append(("DQ-002 silent with COALESCE", check_null_handling("SELECT COALESCE(b.x,0) FROM a LEFT JOIN b ON a.id=b.id"), None))
cases.append(("DQ-002 silent INNER JOIN", check_null_handling("SELECT a.x FROM a INNER JOIN b ON a.id=b.id"), None))

# DQ-003
ID_STRING_SQL = "SELECT id FROM t WHERE customer_id = '123'"
cases.append(("DQ-003 fires _id = string", check_implicit_cast(ID_STRING_SQL), "DQ-003"))
INT_CMP_SQL = "SELECT id FROM t WHERE customer_id = 123"
cases.append(("DQ-003 silent numeric compare", check_implicit_cast(INT_CMP_SQL), None))

# DQ-004
cases.append(("DQ-004 fires INSERT no dedup", check_missing_dedup("INSERT INTO tgt SELECT a,b FROM src"), "DQ-004"))
cases.append(("DQ-004 silent DISTINCT", check_missing_dedup("INSERT INTO tgt SELECT DISTINCT a,b FROM src"), None))
cases.append(("DQ-004 silent no write", check_missing_dedup("SELECT a FROM src"), None))

# DQ-005
cases.append(("DQ-005 fires no WHERE", check_unfiltered_scan("SELECT a FROM big_table"), "DQ-005"))
cases.append(("DQ-005 silent partition filter", check_unfiltered_scan("SELECT a FROM t WHERE _PARTITIONDATE = CURRENT_DATE"), None))
cases.append(("DQ-005 silent WHERE present", check_unfiltered_scan("SELECT a FROM t WHERE id = 1"), None))

# DQ-006
cases.append(("DQ-006 fires CROSS JOIN", check_cross_join("SELECT * FROM a CROSS JOIN b"), "DQ-006"))
cases.append(("DQ-006 silent proper INNER JOIN", check_cross_join("SELECT * FROM a INNER JOIN b ON a.id=b.id"), None))

# DQ-007
DATE_SQL = "SELECT * FROM t WHERE dt = '2023-01-15'"
cases.append(("DQ-007 fires hardcoded date", check_hardcoded_literals(DATE_SQL), "DQ-007"))
MAGIC_SQL = "SELECT * FROM t WHERE amount > 50000"
cases.append(("DQ-007 fires magic number", check_hardcoded_literals(MAGIC_SQL), "DQ-007"))
SMALL_SQL = "SELECT * FROM t WHERE id = 42"
cases.append(("DQ-007 silent small number", check_hardcoded_literals(SMALL_SQL), None))

# DQ-008
cases.append(("DQ-008 fires bare INSERT", check_idempotency("INSERT INTO tgt SELECT a FROM src"), "DQ-008"))
DELETE_INSERT = "DELETE FROM tgt WHERE dt = @dt; INSERT INTO tgt SELECT a FROM src"
cases.append(("DQ-008 silent DELETE guard", check_idempotency(DELETE_INSERT), None))
FULL_MERGE = (
    "MERGE tgt USING src ON tgt.id=src.id "
    "WHEN MATCHED THEN UPDATE SET x=src.x "
    "WHEN NOT MATCHED THEN INSERT VALUES(src.x)"
)
cases.append(("DQ-008 silent full MERGE", check_idempotency(FULL_MERGE), None))
cases.append(("DQ-008 silent no write", check_idempotency("SELECT a FROM src"), None))

# Rule registry size
cases.append(("ALL_RULES contains 8 entries", ALL_RULES, "__len8__"))

all_passed = True
for desc, findings, expected_id in cases:
    if expected_id == "__len8__":
        passed = len(findings) == 8
    elif expected_id is None:
        passed = len(findings) == 0
    else:
        passed = len(findings) >= 1 and any(f.rule_id == expected_id for f in findings)

    icon = "  PASS" if passed else "  FAIL"
    print(f"{icon}  {desc}")
    if not passed:
        all_passed = False
        if expected_id not in (None, "__len8__"):
            print(f"         expected rule_id={expected_id}, got findings={[f.rule_id for f in findings]}")
        elif expected_id is None:
            print(f"         expected 0 findings, got {len(findings)}: {[(f.rule_id, f.title) for f in findings]}")

print()
total = len(cases)
passed_count = sum(1 for desc, findings, exp in cases if (
    (exp == "__len8__" and len(findings) == 8) or
    (exp is None and len(findings) == 0) or
    (exp not in (None, "__len8__") and len(findings) >= 1 and any(f.rule_id == exp for f in findings))
))
print(f"Results: {passed_count}/{total} passed")
sys.exit(0 if all_passed else 1)
