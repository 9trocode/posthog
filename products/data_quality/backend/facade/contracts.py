"""
Contract types for data_quality.

Frozen, framework-free values other products need. No Django imports.
"""

# The registered name of the check-suite workflow. Lives here rather than in ``facade.temporal`` so
# a caller in another product (data_modeling's DAG workflow) can start the suite by name without
# importing this product's workflow and activity modules.
CHECK_SUITE_WORKFLOW_NAME = "data-quality-run-suite"
