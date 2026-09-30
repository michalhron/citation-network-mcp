"""Shared fakes for diagnose_connection tests."""

def capability_ok(endpoint, params):
    """Entitled responses for the capability probes, or None for other calls."""
    if params and params.get('view') == 'REF':
        return {'abstracts-retrieval-response': {'references': {'@total-references': '1'}}}
    if endpoint.startswith('content/article/'):
        return {'full-text-retrieval-response': {'originalText': 'x' * 6000}}
    if endpoint.startswith('content/serial/'):
        return {'serial-metadata-response': {'entry': [{'dc:title': 'MISQ'}]}}
    return None
