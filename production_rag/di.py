"""Application dependency injection container.

Registers all components with the injector. New enterprise components
(DatabaseComponent, RedisComponent) are added alongside existing ones.
All existing bindings are preserved.
"""

from injector import Injector

from production_rag.components.cache.redis_component import (
    RedisComponent,
    create_redis_component,
)
from production_rag.components.database.database_component import DatabaseComponent
from production_rag.settings.settings import Settings, unsafe_typed_settings


def create_application_injector() -> Injector:
    _injector = Injector(auto_bind=True)

    # ---- Existing bindings (unchanged) ----
    _injector.binder.bind(Settings, to=unsafe_typed_settings)

    # ---- Phase 1: PostgreSQL ----
    db_component = DatabaseComponent(unsafe_typed_settings)
    _injector.binder.bind(DatabaseComponent, to=db_component)

    # ---- Phase 2: Redis ----
    redis_component = create_redis_component(unsafe_typed_settings)
    _injector.binder.bind(RedisComponent, to=redis_component)

    return _injector


"""
Global injector for the application.

Avoid using this reference, it will make your code harder to test.

Instead, use the `request.state.injector` reference, which is bound to every request
"""
global_injector: Injector = create_application_injector()
