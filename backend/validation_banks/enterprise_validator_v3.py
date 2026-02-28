"""
ENTERPRISE-GRADE LENDER VALIDATION v4.0 - FIXED
================================================
Production-ready validator with CORRECTED string normalization.

FIXES IN v4.0:
1. ✅ Fixed normalize_intensity() - removes ALL whitespace
2. ✅ Changed operating_states from jsonb to text[] (PostgreSQL arrays)
3. ✅ Better deduplication logic
4. ✅ More accurate confidence scores

All previous security fixes maintained.
"""

import csv
import json
import re
import logging
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, asdict
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# CONFIGURATION MANAGEMENT
# ══════════════════════════════════════════════════════════════

class ValidationConfig:
    """Centralized configuration management"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or 'validation_config.json'
        self.load_config()
    
    def load_config(self):
        """Load configuration from JSON file"""
        default_config = {
            'known_pan_india_banks': [
                'state bank of india',
                'hdfc bank',
                'icici bank',
                'axis bank',
                'bank of baroda',
                'punjab national bank',
                'canara bank',
                'union bank of india',
                'bank of india',
                'kotak mahindra bank',
            ],
            'known_single_state_foreign_banks': [
                'sonali bank',
                'industrial bank of korea',
            ],
            'lab_indicators': [
                r'\bLocal Area Bank\b',
                r'\bLAB\b',
            ],
            'pan_india_min_states': 25,
            'psu_min_aum': 50000,
            'micro_bank_threshold': 500,
            'valid_intensities': ['panindia', 'regional', 'singlestate'],  # Pre-normalized
        }
        
        config_path = Path(self.config_file)
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    loaded_config = json.load(f)
                    default_config.update(loaded_config)
                    logging.info(f"Loaded configuration from {self.config_file}")
            except Exception as e:
                logging.warning(f"Could not load config file: {e}. Using defaults.")
        else:
            try:
                with open(config_path, 'w') as f:
                    json.dump(default_config, f, indent=2)
                logging.info(f"Created default config: {self.config_file}")
            except Exception as e:
                logging.warning(f"Could not save default config: {e}")
        
        # Store config
        self.known_pan_india = [s.lower() for s in default_config['known_pan_india_banks']]
        self.known_single_state_foreign = [s.lower() for s in default_config['known_single_state_foreign_banks']]
        self.lab_indicators = default_config['lab_indicators']
        self.pan_india_min_states = default_config['pan_india_min_states']
        self.psu_min_aum = default_config['psu_min_aum']
        self.micro_bank_threshold = default_config['micro_bank_threshold']
        self.valid_intensities = default_config['valid_intensities']

# Global config instance
CONFIG = ValidationConfig()

# ══════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ══════════════════════════════════════════════════════════════

def setup_logging():
    """Configure production logging with audit trail"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = f'validation_{timestamp}.log'
    audit_file = f'audit_{timestamp}.log'
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    audit_logger = logging.getLogger('audit')
    audit_handler = logging.FileHandler(audit_file)
    audit_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    
    return log_file, audit_file

# ══════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    lender_id: int
    lender_name: str
    company_type: str
    rule: str
    severity: str
    priority: int
    message: str
    business_logic: str
    fix: str
    confidence: int
    category: str

SEVERITY_PRIORITY = {
    'CRITICAL': 1,
    'ERROR': 2,
    'WARNING': 3,
    'INFO': 4
}

# ══════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════

def normalize_name(name: str) -> str:
    """Normalize company name for comparison"""
    normalized = re.sub(r'[^\w\s]', '', name.lower())
    normalized = ' '.join(normalized.split())
    return normalized

def normalize_intensity(intensity: str) -> str:
    """
    ✅ FIX v4.0: CORRECTED normalization - removes ALL whitespace
    
    Handles ALL variants:
    - "Pan India" → "panindia"
    - "pan-india" → "panindia"
    - "PAN INDIA" → "panindia"
    - "Pan_India" → "panindia"
    - "Single State" → "singlestate"
    - "single-state" → "singlestate"
    """
    if not intensity:
        return ''
    
    normalized = intensity.lower()
    normalized = normalized.replace('-', '')
    normalized = normalized.replace('_', '')
    normalized = ''.join(normalized.split())  # ✅ FIX: Remove ALL whitespace!
    
    return normalized

def safe_json_parse(json_str: str, default=None) -> any:
    """Safe JSON parsing with type validation"""
    if default is None:
        default = []
    
    if not json_str or json_str.strip() == '':
        return default
    
    try:
        parsed = json.loads(json_str)
        
        if isinstance(default, list) and not isinstance(parsed, list):
            logging.warning(f"Expected list, got {type(parsed).__name__}: {str(parsed)[:50]}")
            return default
        
        return parsed
        
    except json.JSONDecodeError as e:
        logging.warning(f"Invalid JSON: {json_str[:50]}... Error: {e}")
        return default
    except Exception as e:
        logging.error(f"Unexpected error parsing JSON: {e}")
        return default

def parse_pg_array(array_str: str) -> List[str]:
    """
    ✅ NEW v4.0: Parse PostgreSQL text[] format
    
    Handles formats like:
    - {State1,State2,State3}
    - ["State1","State2","State3"]
    - '["State1", "State2"]'
    """
    if not array_str or array_str.strip() == '':
        return []
    
    array_str = array_str.strip()
    
    # PostgreSQL array format: {State1,State2}
    if array_str.startswith('{') and array_str.endswith('}'):
        content = array_str[1:-1]
        if not content:
            return []
        # Split by comma, handle quoted values
        states = []
        for item in content.split(','):
            item = item.strip().strip('"').strip("'")
            if item:
                states.append(item)
        return states
    
    # JSON array format: ["State1","State2"]
    try:
        parsed = json.loads(array_str)
        if isinstance(parsed, list):
            return parsed
    except:
        pass
    
    # Fallback: try to parse as JSON
    return safe_json_parse(array_str, default=[])

def safe_bool_parse(value: any) -> bool:
    """Safe boolean parsing"""
    if value is None:
        return False
    str_value = str(value).lower().strip()
    return str_value in ('true', 't', '1', 'yes', 'y')

def safe_float(value: any) -> Optional[float]:
    """Safely convert to float"""
    if value in (None, '', 'None', 'null', 'NULL'):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def safe_int(value: any) -> Optional[int]:
    """Safe integer parsing (SQL injection prevention)"""
    if value in (None, '', 'None', 'null', 'NULL'):
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None

def sanitize_sql_identifier(identifier: str) -> str:
    """Sanitize SQL identifiers (additional safety layer)"""
    if not identifier:
        return ''
    sanitized = re.sub(r'[^a-zA-Z0-9_\- &]', '', str(identifier))
    return sanitized

# ══════════════════════════════════════════════════════════════
# VALIDATION RULES
# ══════════════════════════════════════════════════════════════

def validate_lab_constraints(lender: Dict) -> List[ValidationResult]:
    """Validate Local Area Bank constraints"""
    issues = []
    name = lender['company_name']
    
    is_lab = any(re.search(pattern, name, re.I) for pattern in CONFIG.lab_indicators)
    
    if not is_lab:
        return issues
    
    num_states = len(lender.get('operating_states', []))
    intensity = lender.get('operating_intensity', '')
    pan_india = lender.get('pan_india', False)
    
    norm_intensity = normalize_intensity(intensity)
    
    if pan_india or norm_intensity == 'panindia':
        # ✅ v4.0: Generate correct SQL for text[] type
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='LAB_CANNOT_BE_PAN_INDIA',
            severity='CRITICAL',
            priority=SEVERITY_PRIORITY['CRITICAL'],
            message=f'LAB marked as Pan-India violates RBI regulations',
            business_logic='RBI mandates LABs operate in max 3 contiguous districts within one state. Pan-India presence is legally impossible per RBI licensing requirements.',
            fix=f"UPDATE lenders SET operating_states = ARRAY['{sanitize_sql_identifier(lender.get('hq_state', 'Unknown'))}'], operating_intensity = 'Single State', pan_india = false WHERE id = {lender['id']};",
            confidence=100,
            category='LAB_Regulatory'
        ))
    
    if num_states > 2:
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='LAB_MULTI_STATE',
            severity='CRITICAL',
            priority=SEVERITY_PRIORITY['CRITICAL'],
            message=f'LAB operating in {num_states} states (max allowed: 1 state, max 3 districts)',
            business_logic='LABs are district-level banks per RBI guidelines. Multi-state operation violates their banking charter and RBI license conditions.',
            fix=f"UPDATE lenders SET operating_states = ARRAY['{sanitize_sql_identifier(lender.get('hq_state', 'Unknown'))}'], operating_intensity = 'Single State' WHERE id = {lender['id']};",
            confidence=100,
            category='LAB_Regulatory'
        ))
    
    return issues

def validate_psu_bank_constraints(lender: Dict) -> List[ValidationResult]:
    """Validate PSU Bank constraints"""
    issues = []
    
    if lender['company_type'] != 'PSU Bank':
        return issues
    
    num_states = len(lender.get('operating_states', []))
    intensity = lender.get('operating_intensity', '')
    aum = lender.get('aum_crores')
    name = lender['company_name']
    
    norm_intensity = normalize_intensity(intensity)
    
    # ✅ v4.0: FIXED - now correctly checks normalized values
    if norm_intensity != 'panindia':  # Now works! "Pan India" → "panindia"
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='PSU_MUST_BE_PAN_INDIA',
            severity='CRITICAL',
            priority=SEVERITY_PRIORITY['CRITICAL'],
            message=f'PSU Bank not marked Pan-India (current: "{intensity}")',
            business_logic='All nationalized banks operate nationwide per government banking policy post-nationalization. No PSU bank is regional by definition.',
            fix=f"UPDATE lenders SET operating_intensity = 'Pan India', pan_india = true WHERE id = {lender['id']};",
            confidence=100,
            category='PSU_Policy'
        ))
    elif num_states < CONFIG.pan_india_min_states:
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='PSU_INSUFFICIENT_STATES',
            severity='ERROR',
            priority=SEVERITY_PRIORITY['ERROR'],
            message=f'PSU Bank has only {num_states} states (expected {CONFIG.pan_india_min_states}+)',
            business_logic=f'PSU banks operate nationwide. {num_states} states is incomplete data. Expected at least {CONFIG.pan_india_min_states} of 28 states.',
            fix=f'-- Manual verification: Check branch locator and update operating_states',
            confidence=90,
            category='PSU_Data_Quality'
        ))
    
    if aum and aum < CONFIG.psu_min_aum:
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='PSU_SMALL_AUM_SUSPICIOUS',
            severity='WARNING',
            priority=SEVERITY_PRIORITY['WARNING'],
            message=f'PSU Bank with AUM only ₹{aum:,.0f} Cr seems too small',
            business_logic=f'Smallest PSU bank has AUM ~₹175,000 Cr. Values <₹{CONFIG.psu_min_aum:,} Cr likely indicate outdated or incorrect data.',
            fix='-- Manual verification: Check latest annual report and update AUM',
            confidence=85,
            category='PSU_Data_Quality'
        ))
    
    return issues

def validate_operating_geography_logic(lender: Dict) -> List[ValidationResult]:
    """Validate operating geography makes business sense"""
    issues = []
    
    aum = lender.get('aum_crores')
    num_states = len(lender.get('operating_states', []))
    intensity = lender.get('operating_intensity', '')
    name = lender['company_name']
    company_type = lender['company_type']
    
    norm_intensity = normalize_intensity(intensity)
    
    if aum and aum < CONFIG.micro_bank_threshold and norm_intensity == 'panindia':
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=company_type,
            rule='MICRO_BANK_PAN_INDIA_UNLIKELY',
            severity='WARNING',
            priority=SEVERITY_PRIORITY['WARNING'],
            message=f'Bank with AUM ₹{aum:,.0f} Cr claims Pan-India presence',
            business_logic='Pan-India branch operations typically require AUM of ₹5,000+ Cr for economic viability. However, digital/fintech lenders may operate pan-India with lower AUM.',
            fix='-- Manual review: Verify if digital-only vs branch-based model',
            confidence=70,
            category='Geography_Business_Logic'
        ))
    
    if num_states <= 1 and norm_intensity == 'regional':
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=company_type,
            rule='ONE_STATE_MARKED_REGIONAL',
            severity='ERROR',
            priority=SEVERITY_PRIORITY['ERROR'],
            message=f'Only {num_states} state but marked Regional',
            business_logic='"Regional" implies multi-state presence. One state = "Single State" by definition. This is a data entry error.',
            fix=f"UPDATE lenders SET operating_intensity = 'Single State' WHERE id = {lender['id']};",
            confidence=100,
            category='Geography_Consistency'
        ))
    
    return issues

def validate_known_entities(lender: Dict) -> List[ValidationResult]:
    """Validate against known facts about specific banks"""
    issues = []
    name = lender['company_name']
    normalized_name = normalize_name(name)
    norm_intensity = normalize_intensity(lender.get('operating_intensity', ''))
    
    is_known_pan_india = any(known in normalized_name for known in CONFIG.known_pan_india)
    
    if is_known_pan_india and norm_intensity != 'panindia':  # ✅ Fixed!
        matched_bank = next(k for k in CONFIG.known_pan_india if k in normalized_name)
        
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='KNOWN_PAN_INDIA_BANK_WRONG',
            severity='CRITICAL',
            priority=SEVERITY_PRIORITY['CRITICAL'],
            message=f'{name} is a known Pan-India bank but marked as "{lender.get("operating_intensity")}"',
            business_logic=f'This is one of India\'s largest banks ({matched_bank}) with verifiable nationwide presence. This is public knowledge.',
            fix=f"UPDATE lenders SET operating_intensity = 'Pan India', pan_india = true WHERE id = {lender['id']};",
            confidence=100,
            category='Known_Entities'
        ))
    
    is_known_single_state = any(known in normalized_name for known in CONFIG.known_single_state_foreign)
    
    if is_known_single_state and lender.get('pan_india'):
        matched_bank = next(k for k in CONFIG.known_single_state_foreign if k in normalized_name)
        hq_state = sanitize_sql_identifier(lender.get('hq_state', 'Unknown'))
        
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=name,
            company_type=lender['company_type'],
            rule='KNOWN_SINGLE_STATE_WRONG',
            severity='CRITICAL',
            priority=SEVERITY_PRIORITY['CRITICAL'],
            message=f'{name} is a single-office foreign bank but marked Pan-India',
            business_logic=f'{matched_bank.title()} has only 1-2 representative offices in India per RBI\'s foreign bank registry.',
            fix=f"UPDATE lenders SET operating_states = ARRAY['{hq_state}'], operating_intensity = 'Single State', pan_india = false WHERE id = {lender['id']};",
            confidence=95,
            category='Known_Entities'
        ))
    
    return issues

def validate_hq_vs_operations(lender: Dict) -> List[ValidationResult]:
    """Validate HQ location consistency"""
    issues = []
    
    hq_state = lender.get('hq_state', '')
    op_states = lender.get('operating_states', [])
    
    if hq_state and op_states and hq_state not in op_states:
        safe_hq = sanitize_sql_identifier(hq_state)
        
        issues.append(ValidationResult(
            lender_id=lender['id'],
            lender_name=lender['company_name'],
            company_type=lender['company_type'],
            rule='HQ_NOT_IN_OPERATING_STATES',
            severity='ERROR',
            priority=SEVERITY_PRIORITY['ERROR'],
            message=f'HQ in {hq_state} but operating states don\'t include it',
            business_logic='Banks must operate in their headquarters state. This is a regulatory requirement and common sense.',
            fix=f"UPDATE lenders SET operating_states = operating_states || ARRAY['{safe_hq}'] WHERE id = {lender['id']};",
            confidence=100,
            category='HQ_Consistency'
        ))
    
    return issues

# ══════════════════════════════════════════════════════════════
# DEDUPLICATION & CONFLICT RESOLUTION
# ══════════════════════════════════════════════════════════════

def deduplicate_issues(issues: List[ValidationResult]) -> List[ValidationResult]:
    """Deduplicate issues per lender with priority-based conflict resolution"""
    by_lender = defaultdict(list)
    for issue in issues:
        by_lender[issue.lender_id].append(issue)
    
    deduplicated = []
    
    for lender_id, lender_issues in by_lender.items():
        lender_issues.sort(key=lambda x: (x.priority, -x.confidence))
        
        seen_rules = set()
        
        for issue in lender_issues:
            if issue.rule in seen_rules:
                audit_logger = logging.getLogger('audit')
                audit_logger.info(f"Skipped duplicate: {issue.rule} for Lender {lender_id}")
                continue
            
            seen_rules.add(issue.rule)
            deduplicated.append(issue)
    
    logging.info(f"Deduplication: {len(issues)} → {len(deduplicated)} issues")
    return deduplicated

# ══════════════════════════════════════════════════════════════
# VALIDATION ENGINE
# ══════════════════════════════════════════════════════════════

def run_all_validations(lenders: List[Dict]) -> List[ValidationResult]:
    """Run all business logic validations"""
    logging.info(f"Starting validation on {len(lenders)} lenders")
    
    all_issues = []
    
    for i, lender in enumerate(lenders, 1):
        if i % 100 == 0:
            logging.info(f"Validated {i}/{len(lenders)} lenders...")
        
        try:
            all_issues.extend(validate_lab_constraints(lender))
            all_issues.extend(validate_psu_bank_constraints(lender))
            all_issues.extend(validate_operating_geography_logic(lender))
            all_issues.extend(validate_known_entities(lender))
            all_issues.extend(validate_hq_vs_operations(lender))
        except Exception as e:
            logging.error(f"Error validating {lender.get('company_name', 'Unknown')} (ID: {lender.get('id')}): {e}")
    
    all_issues = deduplicate_issues(all_issues)
    
    logging.info(f"Validation complete. Found {len(all_issues)} issues after deduplication.")
    return all_issues

# ══════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════

def generate_comprehensive_report(issues: List[ValidationResult]):
    """Generate detailed report"""
    
    logging.info("\n" + "═" * 100)
    logging.info("BUSINESS LOGIC VALIDATION REPORT - v4.0 FIXED")
    logging.info("═" * 100)
    
    total_issues = len(issues)
    by_severity = Counter(i.severity for i in issues)
    by_category = Counter(i.category for i in issues)
    
    logging.info(f"\nTotal Issues: {total_issues}")
    logging.info(f"By Severity: {dict(by_severity)}")
    logging.info(f"By Category: {dict(by_category)}")
    logging.info("")
    
    by_severity_dict = defaultdict(list)
    for issue in issues:
        by_severity_dict[issue.severity].append(issue)
    
    for severity in ['CRITICAL', 'ERROR', 'WARNING', 'INFO']:
        if severity not in by_severity_dict:
            continue
        
        issues_list = by_severity_dict[severity]
        logging.info(f"\n{'='*100}")
        logging.info(f"{severity}: {len(issues_list)} issues")
        logging.info(f"{'='*100}\n")
        
        by_rule_dict = defaultdict(list)
        for issue in issues_list:
            by_rule_dict[issue.rule].append(issue)
        
        for rule_name, rule_issues in sorted(by_rule_dict.items()):
            logging.info(f"  {rule_name}: {len(rule_issues)} occurrences")
            
            for issue in rule_issues[:5]:
                logging.info(f"    • {issue.lender_name} (ID: {issue.lender_id})")
                logging.info(f"      {issue.message}")
                if issue.confidence >= 90:
                    logging.info(f"      ✅ High confidence fix available")
            
            if len(rule_issues) > 5:
                logging.info(f"    ... and {len(rule_issues) - 5} more\n")
    
    logging.info("\n" + "═" * 100)
    logging.info("AUTO-FIX SQL SCRIPT (Confidence >= 90% only)")
    logging.info("═" * 100 + "\n")
    
    auto_fix_count = 0
    for issue in issues:
        if issue.severity in ['CRITICAL', 'ERROR'] and issue.confidence >= 90 and 'UPDATE' in issue.fix:
            logging.info(f"-- {issue.rule}: {issue.lender_name} (Confidence: {issue.confidence}%)")
            logging.info(issue.fix)
            logging.info("")
            auto_fix_count += 1
    
    logging.info(f"Total auto-fixable: {auto_fix_count}")
    
    csv_path = 'validation_results.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['ID', 'Lender', 'Type', 'Rule', 'Category', 'Severity', 'Message', 'Fix', 'Confidence'])
        for issue in issues:
            writer.writerow([
                issue.lender_id,
                issue.lender_name,
                issue.company_type,
                issue.rule,
                issue.category,
                issue.severity,
                issue.message,
                issue.fix,
                issue.confidence
            ])
    
    logging.info(f"\nFull report: {csv_path}")

# ══════════════════════════════════════════════════════════════
# CSV LOADER
# ══════════════════════════════════════════════════════════════

def load_csv(filepath: str) -> List[Dict]:
    """Load and parse CSV with full error handling"""
    lenders = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row_num, row in enumerate(reader, 1):
            try:
                lender = {
                    'id': safe_int(row.get('id')),
                    'company_name': row.get('company_name', '').strip(),
                    'company_type': row.get('company_type', '').strip(),
                    'aum_crores': safe_float(row.get('aum_crores')),
                    'operating_states': parse_pg_array(row.get('operating_states', '')),  # ✅ v4.0: Parse PostgreSQL arrays
                    'operating_intensity': row.get('operating_intensity', '').strip(),
                    'pan_india': safe_bool_parse(row.get('pan_india')),
                    'hq_state': row.get('hq_state', '').strip(),
                }
                
                if not lender['id'] or not lender['company_name']:
                    continue
                
                lenders.append(lender)
                
            except Exception as e:
                logging.error(f"Row {row_num}: Error - {e}")
                continue
    
    logging.info(f"Loaded {len(lenders)} lenders")
    return lenders

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    import sys
    
    log_file, audit_file = setup_logging()
    
    if len(sys.argv) < 2:
        logging.error("Usage: python validator_v4.py <csv_file> [config_file]")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    config_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    global CONFIG
    if config_file:
        CONFIG = ValidationConfig(config_file)
    
    logging.info("="*100)
    logging.info("ENTERPRISE LENDER VALIDATOR v4.0 - FIXED")
    logging.info("="*100)
    logging.info(f"Log: {log_file}")
    logging.info(f"Audit: {audit_file}")
    logging.info(f"Config: {CONFIG.config_file}")
    logging.info("")
    
    try:
        lenders = load_csv(csv_file)
        issues = run_all_validations(lenders)
        generate_comprehensive_report(issues)
        
        logging.info("\n" + "="*100)
        logging.info(f"COMPLETE: {len(issues)} issues found")
        logging.info("="*100 + "\n")
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()