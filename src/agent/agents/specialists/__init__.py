"""Specialist agent implementations.

Importing this package auto-registers all specialist agents with the
registry.  To add a new specialist:

1. Create a new module in this package (e.g. ``my_specialist.py``)
2. Define a class extending ``BaseSpecialist``
3. Add an import and ``register_specialist()`` call below
"""

from agent.agents.registry import register_specialist
from agent.agents.specialists.analysis import AnalysisSpecialist
from agent.agents.specialists.commentary import CommentarySpecialist
from agent.agents.specialists.data import DataSpecialist
from agent.agents.specialists.financial import FinancialAnalystSpecialist
from agent.agents.specialists.news import NewsSpecialist
from agent.agents.specialists.report import ReportSpecialist

register_specialist(DataSpecialist())
register_specialist(AnalysisSpecialist())
register_specialist(FinancialAnalystSpecialist())
register_specialist(NewsSpecialist())
register_specialist(CommentarySpecialist())
register_specialist(ReportSpecialist())
