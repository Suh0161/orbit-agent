from typing import Dict, Set, Optional
from enum import Enum

class PermissionLevel(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

class PermissionManager:
    def __init__(self, config: Dict[str, str] = None):
        self.policy: Dict[str, PermissionLevel] = {
            "file_read": PermissionLevel.ALLOW,
            "file_write": PermissionLevel.ASK,  # Default safe
            "shell_exec": PermissionLevel.ALLOW,
            "net_access": PermissionLevel.ALLOW,
            "code_scaffold": PermissionLevel.ALLOW,
            "desktop_control": PermissionLevel.ALLOW,
            "desktop_view": PermissionLevel.ALLOW,
            "vision_analyze": PermissionLevel.ALLOW
        }
        if config:
            for k, v in config.items():
                try:
                    self.policy[k] = PermissionLevel(v)
                except:
                    pass

    def check_permission(self, permission: str, step_approved: bool = False) -> bool:
        """
        Check if an action is allowed.
        If step_approved is True, acts as an override for ASK.
        """
        level = self.policy.get(permission, PermissionLevel.DENY) # Deny unknown
        
        if level == PermissionLevel.ALLOW:
            return True
        if level == PermissionLevel.DENY:
            return False
        if level == PermissionLevel.ASK:
            return step_approved
            
        return False
        
    def requires_approval(self, permission: str) -> bool:
        return self.policy.get(permission, PermissionLevel.DENY) == PermissionLevel.ASK
