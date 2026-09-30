"""Optional cache factories, shared by the HTTP and virtual RW engines.

Entry points register extensions before invoking the existing engine. Importing
the proposed engine alone preserves its original policy set and dependencies.
Factories accept (capacity, sizer); graph preprocessing belongs to the caller.
"""

FACTORIES = {}


def register(name, factory):
    FACTORIES[name] = factory
