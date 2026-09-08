"""Generated stage constants; each build contains only its allowed inputs."""
STAGE = STAGE_VALUE
GUARDED = GUARD_VALUE
LEVEL = STAGE[0]

from workers import Response, WorkerEntrypoint
from contextlib import nullcontext
import json
from urllib.parse import urlsplit

if GUARDED:
    # Exact existing guard via read-only symlink, not an altered reproduction.
    from side_effect_guard import guard
else:
    def guard():
        return nullcontext(None)

with guard() as IMPORT_COUNTS:
    from app import app
    import app as app_module

if LEVEL >= 'E':
    from workers import wsgi
    from flask import jsonify, request
    ORIGINAL_WSGI = app_module._flask_wsgi_app
    if LEVEL >= 'F':
        ROUTES = sorted([{'rule':r.rule,'endpoint':r.endpoint,'methods':sorted(r.methods)}
                         for r in app.url_map.iter_rules()], key=lambda r:(r['rule'],r['endpoint']))

    def fixed():
        return 'fixed response'

    app.add_url_rule('/wsgi', 'stability_fixed', fixed)
    ALLOWED = {'/wsgi'}
    if LEVEL >= 'F':
        app.add_url_rule('/url-map', 'stability_map', lambda: jsonify(routes=ROUTES))
        ALLOWED.add('/url-map')
    if LEVEL >= 'G':
        def template_probe():
            mode = request.path.rsplit('/',1)[-1]
            env = app.jinja_env
            source = env.loader.get_source(env, 'probe.html')[0]
            if mode == 'load':
                return jsonify(operation=mode, source=source)
            if mode == 'compile':
                env.compile(source, name='probe.html')
                return jsonify(operation=mode, compiled=True)
            if mode == 'render':
                return env.from_string(source).render(message='stability')
            if mode == 'include':
                # Recompile on each request to measure the actual operation.
                env.cache.clear()
                return env.get_template('upload_csv.html').render(mode='admin')
            if mode == 'full':
                from jinja2 import meta
                if STAGE != 'H-cached':
                    env.cache.clear()
                names = sorted(env.list_templates())
                if STAGE == 'H-cached':
                    names = [name for name in names if name != 'probe.html']
                for name in names:
                    text = env.loader.get_source(env,name)[0]
                    list(meta.find_referenced_templates(env.parse(text)))
                    env.get_template(name)
                return env.get_template('upload_csv.html').render(mode='admin')
            return 'Not found',404
        for operation in ['load','compile','render','include','full']:
            path='/template/'+operation
            app.add_url_rule(path,'stability_'+operation,template_probe)
            ALLOWED.add(path)
    if LEVEL == 'J':
        ALLOWED.update(['/static/participants_template.csv','/static/cards/c8.png'])

    def safe_wsgi(environ, start_response):
        state={}
        def capture(status,headers,exc_info=None):
            state.update(status=status,headers=headers)
        with guard() as counts:
            result=ORIGINAL_WSGI(environ,capture)
            try:
                chunks=list(result)
            finally:
                if hasattr(result,'close'):
                    result.close()
        if counts is not None and any(counts.values()):
            state={'status':'500 Internal Server Error','headers':[]}
            chunks=[b'Blocked side effect']
        state['headers'].extend([('X-Stability-Stage',STAGE),('X-Stability-Guard',str(GUARDED)),
                                 ('X-Stability-Effects',json.dumps(counts)),
                                 ('X-Stability-Import-Effects',json.dumps(IMPORT_COUNTS))])
        start_response(state['status'],state['headers'])
        return chunks


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path=urlsplit(request.url).path
        if str(request.method) != 'GET':
            return Response('Method not allowed',status=405)
        self.seen=getattr(self,'seen',0)+1
        # A best-effort instance-local marker, not proof of a cold isolate.
        if path=='/raw':
            with guard() as counts:
                assert not app_module._runtime_initialized
                assert app.secret_key is None
                response=Response('fixed response',headers={
                    'X-Stability-Stage':STAGE,'X-Stability-Guard':str(GUARDED),
                    'X-Stability-Effects':json.dumps(counts),
                    'X-Stability-Import-Effects':json.dumps(IMPORT_COUNTS),
                    'X-Stability-Runtime':'false','X-Stability-Invocation':str(self.seen),
                })
            return response
        if LEVEL in 'IJ' and path in ['/assets/participants_template.csv','/assets/cards/c8.png']:
            return await self.env.ASSETS.fetch('https://assets.local/'+path.removeprefix('/assets/'))
        if LEVEL>='E' and path in ALLOWED:
            return await wsgi.fetch(safe_wsgi,request,self.env)
        return Response('Not found',status=404)
