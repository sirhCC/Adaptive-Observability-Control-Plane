"""Rule conflict detection and analysis for policy validation."""
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass
from control_plane.main import Rule, Condition


@dataclass
class RuleConflict:
    """Represents a potential conflict between rules."""
    type: str  # "overlap", "priority_issue", "action_conflict", "unreachable"
    severity: str  # "error", "warning", "info"
    rule_ids: List[str]
    message: str
    suggestion: Optional[str] = None


class RuleConflictDetector:
    """Analyzes rules for potential conflicts and issues."""
    
    def __init__(self, rules: List[Rule]):
        self.rules = rules
        self.conflicts: List[RuleConflict] = []
    
    def analyze(self) -> List[RuleConflict]:
        """Perform comprehensive conflict analysis."""
        self.conflicts = []
        
        self._check_duplicate_ids()
        self._check_scope_overlaps()
        self._check_unreachable_rules()
        self._check_priority_conflicts()
        self._check_always_conditions()
        
        return self.conflicts
    
    def _check_duplicate_ids(self):
        """Check for duplicate rule IDs."""
        seen = {}
        for rule in self.rules:
            if rule.id in seen:
                self.conflicts.append(RuleConflict(
                    type="duplicate_id",
                    severity="error",
                    rule_ids=[rule.id],
                    message=f"Duplicate rule ID '{rule.id}' found",
                    suggestion="Rule IDs must be unique. Rename one of the rules."
                ))
            seen[rule.id] = rule
    
    def _check_scope_overlaps(self):
        """Check for rules with overlapping service/environment scopes."""
        for i, rule1 in enumerate(self.rules):
            for rule2 in self.rules[i + 1:]:
                if self._scopes_overlap(rule1, rule2):
                    # Check if they have different priorities
                    if rule1.priority == rule2.priority:
                        self.conflicts.append(RuleConflict(
                            type="scope_overlap",
                            severity="warning",
                            rule_ids=[rule1.id, rule2.id],
                            message=f"Rules '{rule1.id}' and '{rule2.id}' have overlapping scopes and same priority",
                            suggestion="Rules with overlapping scopes should have different priorities to establish evaluation order."
                        ))
    
    def _check_unreachable_rules(self):
        """Check for rules that may never execute due to earlier 'always' rules."""
        sorted_rules = sorted(self.rules, key=lambda r: r.priority)
        
        always_rules: Dict[Tuple[Optional[str], Optional[str]], List[str]] = {}
        
        for rule in sorted_rules:
            scope = (rule.service, rule.environment)
            
            # Check if this rule has 'always' conditions
            has_always = any(
                c.kind == "always" or c.op == "always"
                for c in rule.conditions
            )
            
            if has_always:
                always_rules.setdefault(scope, []).append(rule.id)
            
            # Check if this rule is blocked by earlier 'always' rules
            for blocking_scope, blocking_ids in always_rules.items():
                if self._scope_contains(blocking_scope, scope) and rule.id not in blocking_ids:
                    self.conflicts.append(RuleConflict(
                        type="unreachable",
                        severity="warning",
                        rule_ids=[blocking_ids[0], rule.id],
                        message=f"Rule '{rule.id}' may be unreachable due to earlier 'always' rule '{blocking_ids[0]}'",
                        suggestion="Consider adjusting priorities or making the 'always' rule more specific."
                    ))
    
    def _check_priority_conflicts(self):
        """Check for potential priority ordering issues."""
        # Group rules by scope
        scope_groups: Dict[Tuple[Optional[str], Optional[str]], List[Rule]] = {}
        
        for rule in self.rules:
            scope = (rule.service, rule.environment)
            scope_groups.setdefault(scope, []).append(rule)
        
        # Check each scope group
        for scope, scope_rules in scope_groups.items():
            if len(scope_rules) > 1:
                sorted_rules = sorted(scope_rules, key=lambda r: r.priority)
                
                # Check for rules with more specific conditions at lower priority
                for i, rule1 in enumerate(sorted_rules):
                    for rule2 in sorted_rules[i + 1:]:
                        if self._is_more_specific(rule1, rule2):
                            self.conflicts.append(RuleConflict(
                                type="priority_issue",
                                severity="info",
                                rule_ids=[rule1.id, rule2.id],
                                message=f"Rule '{rule1.id}' (priority {rule1.priority}) is more specific than '{rule2.id}' (priority {rule2.priority}) but executes first",
                                suggestion="Consider if the priority order matches your intent. More specific rules often run after general rules."
                            ))
    
    def _check_always_conditions(self):
        """Check for potential issues with 'always' conditions."""
        for rule in self.rules:
            has_always = any(c.kind == "always" or c.op == "always" for c in rule.conditions)
            has_other = any(c.kind != "always" and c.op != "always" for c in rule.conditions)
            
            if has_always and has_other:
                self.conflicts.append(RuleConflict(
                    type="always_with_conditions",
                    severity="warning",
                    rule_ids=[rule.id],
                    message=f"Rule '{rule.id}' has 'always' condition combined with other conditions",
                    suggestion="The 'always' condition makes other conditions redundant. Remove either the 'always' or the other conditions."
                ))
    
    def _scopes_overlap(self, rule1: Rule, rule2: Rule) -> bool:
        """Check if two rules have overlapping scopes."""
        # None means wildcard (matches all)
        s1, e1 = rule1.service, rule1.environment
        s2, e2 = rule2.service, rule2.environment
        
        services_overlap = (s1 is None or s2 is None or s1 == s2)
        envs_overlap = (e1 is None or e2 is None or e1 == e2)
        
        return services_overlap and envs_overlap
    
    def _scope_contains(self, scope1: Tuple[Optional[str], Optional[str]], 
                       scope2: Tuple[Optional[str], Optional[str]]) -> bool:
        """Check if scope1 contains scope2 (scope1 is more general)."""
        s1, e1 = scope1
        s2, e2 = scope2
        
        service_contains = (s1 is None or s1 == s2)
        env_contains = (e1 is None or e1 == e2)
        
        return service_contains and env_contains
    
    def _is_more_specific(self, rule1: Rule, rule2: Rule) -> bool:
        """Check if rule1 is more specific than rule2."""
        # More conditions = more specific
        if len(rule1.conditions) > len(rule2.conditions):
            return True
        
        # Specific service/env is more specific than None (wildcard)
        specificity1 = sum([rule1.service is not None, rule1.environment is not None])
        specificity2 = sum([rule2.service is not None, rule2.environment is not None])
        
        return specificity1 > specificity2


def validate_policy_rules(rules: List[Rule]) -> Dict[str, any]:
    """Validate rules and return analysis report."""
    detector = RuleConflictDetector(rules)
    conflicts = detector.analyze()
    
    errors = [c for c in conflicts if c.severity == "error"]
    warnings = [c for c in conflicts if c.severity == "warning"]
    info = [c for c in conflicts if c.severity == "info"]
    
    return {
        "valid": len(errors) == 0,
        "conflicts": conflicts,
        "summary": {
            "total_rules": len(rules),
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(info)
        }
    }
