"""VoID-driven pre-execution guards for the bench harness (one module per guard,
each reads only the endpoint's VoID): autoprefix, classcheck, predicatecheck,
emptydiagnoser, feedback. classcheck/predicatecheck share VoID resolution from autoprefix."""
