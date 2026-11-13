"""The route groups: each class keeps the handlers (and their helpers) of one area.

`application.App` mixes them in; server.py lists the routes themselves.
"""
from routes.status import StatusRoutes
from routes.models import ModelRoutes
from routes.jobs import JobRoutes
from routes.library import LibraryRoutes
from routes.lora import LoraRoutes
from routes.agent import AgentRoutes
from routes.generate import GenerateRoutes
from routes.montage import MontageRoutes

__all__ = [StatusRoutes, ModelRoutes, JobRoutes, LibraryRoutes, LoraRoutes, AgentRoutes, GenerateRoutes, MontageRoutes]
