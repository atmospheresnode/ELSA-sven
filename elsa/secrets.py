# secrets.py


def allowed_hosts_production():
    return [ 'atmos.nmsu.edu', u'atmos.nmsu.edu', 'atmos.nmsu.edu/elsa', 'https://atmos.nmsu.edu', 'https://atmos.nmsu.edu/elsa', u'atmos.nmsu.edu/elsa']

def allowed_hosts_local():
    return [ '127.0.0.1' ]

def secret_key():
    SECRET_KEY = '@s5@4)l6h($i#=znxh!^pu3fjo-&b-v8_us##_jvod*ci&r5x8m'
    return SECRET_KEY


    

