import os
if os.environ.get('HDUE_HOUDINI_ENABLE') == '1':
    import hdefereval
    import hdue_houdini
    hdefereval.execute_deferred(hdue_houdini.start)
