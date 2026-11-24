# Evaluator Validation Record

## Validation scope

- Individual business-rule detection
- Combined business-rule detection
- Reordered business-rule detection
- Deleted-customer rule
- Verified-customer rule
- Common net-revenue invariant (payments minus refunds) in every condition
- One-to-many join multiplication
- Scalar result contract
- Read-only SQL execution
- SQL execution failure classification
- Result-contract failure classification
- Business-rule violation classification
- Evaluator failure classification

## Test execution

Run:

    python -m pytest tests/ -v

Record the actual test result locally after execution.

## Scope limitation

Passing automated tests validates the tested evaluator behaviors.
It does not prove that every possible SQL formulation or business-rule
violation can be detected. Independent manual review and additional
adversarial test cases are still recommended.
