from ipykernel.kernelbase import Kernel
from pexpect import replwrap, EOF
from subprocess import check_output
import re
import signal

__version__ = '0.0.1'

version_pat = re.compile(r'version (\d+(\.\d+)+)')

class GeoScriptGroovyKernel(Kernel):
    implementation = 'geoscript_groovy_kernel'
    implementation_version = __version__

    @property
    def language_version(self):
        m = version_pat.search(self.banner)
        return m.group(1)

    _banner = None

    @property
    def banner(self):
        if self._banner is None:
            self._banner = check_output(['geoscript-groovysh', '--version']).decode('utf-8')
        return self._banner

    language_info = {'name': 'groovy',
                     'codemirror_mode': 'groovy',
                     'mimetype': 'text/groovy',
                     'file_extension': '.groovy'}

    def __init__(self, **kwargs):
        Kernel.__init__(self, **kwargs)
        self._start_geoscript_groovy()

    def _start_geoscript_groovy(self):
        # Signal handlers are inherited by forked processes, and we can't easily
        # reset it from the subprocess. Since kernelapp ignores SIGINT except in
        # message handlers, we need to temporarily reset the SIGINT handler here
        # so that bash and its children are interruptible.
        sig = signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            self.geoscriptgroovywrapper = replwrap.REPLWrapper("geoscript-groovysh --terminal=none", u"groovy:", None)
        finally:
            signal.signal(signal.SIGINT, sig)
            
        # Create a Groovy display function that can display images
        init_script = """
        
        // Set XY coordinate ordering
        System.setProperty("org.geotools.referencing.forceXY", "true")

        // Turn off logging
        java.util.logging.LogManager.getLogManager().reset()
        def globalLogger = java.util.logging.Logger.getLogger(java.util.logging.Logger.GLOBAL_LOGGER_NAME)
        globalLogger.setLevel(java.util.logging.Level.OFF)
        
        display = { obj ->
            if (obj instanceof java.awt.Image) { 
                def out = new ByteArrayOutputStream()
                javax.imageio.ImageIO.write(obj, "png", out)
                String str = javax.xml.bind.DatatypeConverter.printBase64Binary(out.toByteArray())
                "image/png;base64,${str}"
            } 
            else if (obj instanceof geoscript.geom.Geometry) {
                ByteArrayOutputStream out = new ByteArrayOutputStream()
                geoscript.render.Draw.draw(obj as geoscript.geom.Geometry, out:out, imageType: "base64", size:[200,200])
                byte[] bytes = org.apache.commons.codec.binary.Base64.encodeBase64(out.toByteArray())
                String str = new String(bytes, "UTF-8")
                "image/png;base64,${str}"
            }
            else if (obj instanceof geoscript.feature.Feature) {
                ByteArrayOutputStream out = new ByteArrayOutputStream()
                geoscript.render.Draw.draw(obj as geoscript.feature.Feature, out:out, imageType: "base64", size:[200,200])
                byte[] bytes = org.apache.commons.codec.binary.Base64.encodeBase64(out.toByteArray())
                String str = new String(bytes, "UTF-8")
                "image/png;base64,${str}"
            }
            else if (obj instanceof geoscript.layer.Layer) {
                ByteArrayOutputStream out = new ByteArrayOutputStream()
                geoscript.render.Draw.draw(obj as geoscript.layer.Layer, out:out, imageType: "base64", size:[200,200])
                byte[] bytes = org.apache.commons.codec.binary.Base64.encodeBase64(out.toByteArray())
                String str = new String(bytes, "UTF-8")
                "image/png;base64,${str}"
            }
            else {
                obj
            }
        }
        """
        
        self.geoscriptgroovywrapper.run_command(init_script)

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        if not code.strip():
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payload': [], 'user_expressions': {}}

        interrupted = False
        try:
            images = []
            output = self.geoscriptgroovywrapper.run_command(code, timeout=None)
            lines = output.split(u"\r\n")
            lines_to_keep = []
            for line in lines:
                line = line.replace('===> ','')
                line = re.sub(r'[0-9]{3}>','',line)
                line = line.strip()
                if len(line) > 0 and line != 'null':
                    if line.startswith("image/png;base64,"):
                        images.append(line)
                    elif not line.startswith("NetCDF-4 C library not present") and not line.startswith("Unable to load library 'netcdf'"):
                        lines_to_keep.append("%s" % line)
            output = u"\r\n".join(lines_to_keep)
        except KeyboardInterrupt:
            self.geoscriptgroovywrapper.child.sendintr()
            interrupted = True
            self.geoscriptgroovywrapper._expect_prompt()
            output = self.geoscriptgroovywrapper.child.before
        except EOF:
            output = self.geoscriptgroovywrapper.child.before + 'Restarting GeoScript Groovy'
            self._start_geoscript_groovy()

        if not silent:
            # Send standard output
            stream_content = {'name': 'stdout', 'text': output}
            self.send_response(self.iopub_socket, 'stream', stream_content)
            
            # Send images, if any
            for img in images:
                try:
                    data = {
                        'data': {
                            'image/png': img.split('image/png;base64,')[1]
                        },
                        'metadata': {}
                    }
                except ValueError as e:
                    message = {'name': 'stdout', 'text': str(e)}
                    self.send_response(self.iopub_socket, 'stream', message)
                else:
                    self.send_response(self.iopub_socket, 'display_data', data)

        if interrupted:
            return {'status': 'abort', 'execution_count': self.execution_count}

        return {'status': 'ok', 'execution_count': self.execution_count,
                    'payload': [], 'user_expressions': {}}