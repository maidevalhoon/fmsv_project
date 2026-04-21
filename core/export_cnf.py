"""
export_cnf.py — Utility to dump CNF clauses into a physical file in standard DIMACS format.

This was created to support debugging and proof of concept logic for the ATPG system,
allowing inspection of the mathematical structures sent to PySAT.
"""

import os
from core.cnf_builder import build_circuit_cnf

def export_clauses_to_dimacs(all_clauses, out_path, header_comment):
    """
    Writes a list of CNF clauses into a DIMACS formatted file.
    Calculates the variables and appends the '0' endline marker per DIMACS spec.
    """
    max_var = 0
    for clause in all_clauses:
        for lit in clause:
            if abs(lit) > max_var:
                max_var = abs(lit)
                
    num_clauses = len(all_clauses)
    
    with open(out_path, 'w') as f:
        f.write(f"c {header_comment}\n")
        f.write(f"p cnf {max_var} {num_clauses}\n")
        
        for clause in all_clauses:
            clause_str = " ".join(str(lit) for lit in clause) + " 0\n"
            f.write(clause_str)

def dump_good_circuit_cnf(module_name, module_data, cnf_folder):
    """
    Generates and exports exactly the 'Good' (unfaulted) circuit representation mathematically.
    """
    cells = module_data.get("cells", {})
    good_clauses, _, _ = build_circuit_cnf(cells, var_offset=0)
    
    out_path = os.path.join(cnf_folder, "good_circuit.cnf")
    header_comment = f"Good Circuit CNF for {module_name}"
    
    export_clauses_to_dimacs(good_clauses, out_path, header_comment)
    return out_path

def dump_miter_cnf(module_name, fault_net, fault_value, all_clauses, cnf_folder):
    """
    Exports a specific fault's Miter CNF generated during single-fault runs.
    """
    out_path = os.path.join(cnf_folder, f"SA{fault_value}_net{fault_net}.cnf")
    header_comment = f"Miter CNF for {module_name} fault SA{fault_value}@net{fault_net}"
    
    export_clauses_to_dimacs(all_clauses, out_path, header_comment)
    return out_path
