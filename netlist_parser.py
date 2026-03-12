import json
import sys
from pysat.formula import CNF

def parse_yosys_json(json_file):
    """
    Parses a Yosys JSON netlist and converts the circuit logic into
    Conjunctive Normal Form (CNF) for SAT solvers.
    """
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {json_file} not found. Did you run 'yosys synth.ys'?")
        sys.exit(1)

    modules = data.get("modules", {})
    if not modules:
         print("No modules found in the JSON netlist.")
         sys.exit(1)
    
    # Assuming one top module
    top_module_name = list(modules.keys())[0]
    top_module = modules[top_module_name]
    cells = top_module.get("cells", {})

    print(f"--- Generating CNF for Module: {top_module_name} ---")

    # Step 1: Mapping - PySAT strictly requires integers (1, 2, 3...) for variables.
    # Yosys assigns nets numerical IDs like "2" or string names like "A". 
    # We must create a mapping to guarantee every net gets a unique integer > 0
    cnf = CNF()
    net_to_var = {}
    next_var_id = [1] # Use a list to allow mutation inside the nested function

    def get_var(net_id):
        # Sometimes yosys nets are arrays like [2], sometimes strings like "A"
        if isinstance(net_id, list):
            net_id = net_id[0]
            
        # If it's a constant 0 or 1, handle it (Yosys uses strings "0" and "1" for constants)
        if str(net_id) == "0":
            return -1 # Placeholder for False
        if str(net_id) == "1":
            return -2 # Placeholder for True
            
        net_id_str = str(net_id)
        if net_id_str not in net_to_var:
            net_to_var[net_id_str] = next_var_id[0]
            next_var_id[0] += 1
        return net_to_var[net_id_str]

    # Step 2: Apply Tseitin Transformations for each gate
    for cell_name, cell_data in cells.items():
        gate_type = cell_data["type"]
        connections = cell_data["connections"]

        if gate_type == "$_AND_":
            a = get_var(connections["A"])
            b = get_var(connections["B"])
            y = get_var(connections["Y"])
            # Tseitin for Y = A AND B
            # (A v ¬Y) ∧ (B v ¬Y) ∧ (¬A v ¬B v Y)
            if a > 0 and b > 0 and y > 0:
                cnf.append([a, -y])
                cnf.append([b, -y])
                cnf.append([-a, -b, y])

        elif gate_type == "$_OR_":
            a = get_var(connections["A"])
            b = get_var(connections["B"])
            y = get_var(connections["Y"])
            # Tseitin for Y = A OR B
            # (¬A v Y) ∧ (¬B v Y) ∧ (A v B v ¬Y)
            if a > 0 and b > 0 and y > 0:
                cnf.append([-a, y])
                cnf.append([-b, y])
                cnf.append([a, b, -y])

        elif gate_type == "$_NOT_":
            a = get_var(connections["A"])
            y = get_var(connections["Y"])
            # Tseitin for Y = NOT A
            # (A v Y) ∧ (¬A v ¬Y)
            if a > 0 and y > 0:
                cnf.append([a, y])
                cnf.append([-a, -y])
            
        elif gate_type == "$_XOR_":
            a = get_var(connections["A"])
            b = get_var(connections["B"])
            y = get_var(connections["Y"])
            # Tseitin for Y = A XOR B
            # (¬A v ¬B v ¬Y) ∧ (A v B v ¬Y) ∧ (A v ¬B v Y) ∧ (¬A v B v Y)
            if a > 0 and b > 0 and y > 0:
                cnf.append([-a, -b, -y])
                cnf.append([a, b, -y])
                cnf.append([a, -b, y])
                cnf.append([-a, b, y])
            
        else:
            print(f"[WARNING] Unhandled gate type: {gate_type}")

    print(f"[INFO] Successfully mapped {len(net_to_var)} wires to CNF integer variables.")
    print(f"[INFO] Generated {len(cnf.clauses)} CNF clauses representing the circuit logic.")
    
    # Save the CNF to a file so we can inspect it or pass it to a solver
    cnf.to_file("circuit_logic.cnf")
    print("[INFO] Saved CNF formula to 'circuit_logic.cnf'")

if __name__ == "__main__":
    parse_yosys_json("circuit.json")
