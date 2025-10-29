import pyomo.environ as pyo

def build_model(params):
    m = pyo.ConcreteModel()
    m.hours = range(24)
    m.gen = pyo.Var(m.hours, domain=pyo.NonNegativeReals)
    m.cost = pyo.Objective(expr=sum(0.1 * m.gen[h] for h in m.hours))
    m.energy_balance = pyo.ConstraintList()
    for h in m.hours:
        m.energy_balance.add(m.gen[h] >= params.get('load', [0]*24)[h])
    return m
