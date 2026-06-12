"""多人格管理插件的核心模块。"""

from .integrations import IntegrationInspector
from .auto_persona import (
    AUTO_PERSONA_EVENT_KEY,
    EFFECTIVE_PERSONA_EVENT_KEY,
    SELECTED_MODEL_EVENT_KEY,
    SELECTED_PROVIDER_EVENT_KEY,
    AutoPersonaManager,
)
from .memory_isolation import MemoryIsolationManager
from .meme_manager_adapter import MemeManagerPersonaAdapter
from .gitee_aiimg_adapter import GiteeAiimgPersonaAdapter
from .life_schedule_library import LifeScheduleLibraryManager
from .life_scheduler_adapter import LifeSchedulerPersonaAdapter
from .livingmemory_adapter import LivingMemoryPersonaAdapter
from .persona_scheduler import PersonaScheduler
from .session_persona import (
    AstrBotSessionPersonaManager,
    SessionPersonaDiagnostics,
    SessionPersonaResetResult,
)
from .private_companion_adapter import PrivateCompanionProactiveAdapter
from .proactive_chat_adapter import ProactiveChatPersonaAdapter
from .matcher import (
    EventIdentity,
    PermissionResult,
    PolicyDecision,
    PolicyMatcher,
    parse_session_identity,
)
from .permissions import is_group_manager
from .plugin_catalog import (
    collect_plugin_catalog,
    filter_plugin_scope,
    is_protected_plugin,
)
from .runtime_helpers import restore_user_policy_wrappers
from .session_importer import (
    SessionImporter,
    imported_group_rule,
    imported_private_rule,
)
from .persona_adapter import (
    AstrBotPersonaAdapter,
    PersonaApplyResult,
    replace_persona_prompt,
)
from .policy_store import (
    PolicyConfigError,
    PolicyConflictError,
    PolicyStore,
    default_auto_persona,
    default_auto_persona_selector,
    default_gitee_aiimg_effects,
    default_life_pool,
    default_private_companion_proactive,
    default_plugin_access,
    default_meme_isolation,
    default_policy,
    default_proactive_chat_persona_prompts,
)

__all__ = [
    "AstrBotPersonaAdapter",
    "AstrBotSessionPersonaManager",
    "AutoPersonaManager",
    "AUTO_PERSONA_EVENT_KEY",
    "EFFECTIVE_PERSONA_EVENT_KEY",
    "SELECTED_MODEL_EVENT_KEY",
    "SELECTED_PROVIDER_EVENT_KEY",
    "EventIdentity",
    "GiteeAiimgPersonaAdapter",
    "IntegrationInspector",
    "LifeScheduleLibraryManager",
    "LifeSchedulerPersonaAdapter",
    "LivingMemoryPersonaAdapter",
    "MemoryIsolationManager",
    "MemeManagerPersonaAdapter",
    "PersonaScheduler",
    "SessionPersonaDiagnostics",
    "SessionPersonaResetResult",
    "PermissionResult",
    "PolicyConfigError",
    "PolicyConflictError",
    "PolicyDecision",
    "PolicyMatcher",
    "parse_session_identity",
    "PolicyStore",
    "PrivateCompanionProactiveAdapter",
    "ProactiveChatPersonaAdapter",
    "PersonaApplyResult",
    "collect_plugin_catalog",
    "filter_plugin_scope",
    "is_protected_plugin",
    "default_gitee_aiimg_effects",
    "default_auto_persona",
    "default_auto_persona_selector",
    "default_life_pool",
    "default_private_companion_proactive",
    "default_meme_isolation",
    "default_plugin_access",
    "default_policy",
    "default_proactive_chat_persona_prompts",
    "imported_group_rule",
    "imported_private_rule",
    "is_group_manager",
    "replace_persona_prompt",
    "restore_user_policy_wrappers",
    "SessionImporter",
]
