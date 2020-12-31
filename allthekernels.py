"""A Kernel Proxy to restart your kernels in place.

use the %restart magic.


Adapted from MinRK's all the kernels.
"""

import os
import sys

from tornado.ioloop import IOLoop

import zmq
from zmq.eventloop import ioloop
from zmq.eventloop.future import Context

from traitlets import Dict, Unicode

from jupyter_client import KernelManager
from ipykernel.kernelbase import Kernel
from ipykernel.kernelapp import IPKernelApp, IPythonKernel

from IPython.core.usage import default_banner

__version__ = "0.0.0.dev"


from there import print


class SWKM(KernelManager):
    def format_kernel_cmd(self, *args, **kwargs):
        print(f"{args},{kwargs}")
        res = super().format_kernel_cmd(*args, **kwargs)
        print(f"{res=}")
        assert isinstance(res, list), res
        res = ["ipykernel" if x == "allthekernels" else x for x in res]

        assert "allthekernels" not in res
        return res

class KernelProxy(object):
    """A proxy for a single kernel


    Hooks up relay of messages on the shell channel.
    """
    def __init__(self, manager, shell_upstream):
        self.manager = manager
        self.shell = self.manager.connect_shell()
        self.shell_upstream = shell_upstream
        self.iopub_url = self.manager._make_url('iopub')
        IOLoop.current().add_callback(self.relay_shell)

    async def relay_shell(self):
        """Coroutine for relaying any shell replies"""
        while True:
            msg = await self.shell.recv_multipart()
            self.shell_upstream.send_multipart(msg)


class Proxy(Kernel):
    """Kernel class for proxying ALL THE KERNELS YOU HAVE"""

    implementation = "IPython Kernel Restarter"
    implementation_version = __version__
    language_info = {
        "name": "Python",
        "mimetype": "text/python",
    }

    banner = default_banner

    default_kernel = os.environ.get('ATK_DEFAULT_KERNEL') or 'python%i' % (sys.version_info[0])
    _atk_parent = None
    target = Unicode("wuup", config=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.future_context = ctx = Context()
        self.iosub = ctx.socket(zmq.SUB)
        self.iosub.subscribe = b''
        self.shell_stream = self.shell_streams[0]
        self.kernel = None

    def start(self):
        super().start()
        loop = IOLoop.current()
        loop.add_callback(self.relay_iopub_messages)
        self.get_kernel("atk")

    async def relay_iopub_messages(self):
        """Coroutine for relaying IOPub messages from all of our kernels"""
        while True:
            msg = await self.iosub.recv_multipart()
            self.iopub_socket.send_multipart(msg)

    def start_kernel(self, name):
        """Start a new kernel"""
        assert name == "atk"
        base, ext = os.path.splitext(self.parent.connection_file)
        cf = '{base}-{name}{ext}'.format(
            base=base,
            name=name,
            ext=ext,
        )
        self_name = self.target.split("/")[-2]
        print(f"self is: ", self_name)
        print(f"{cf=}")
        manager = SWKM(
            kernel_name=name,
            session=self.session,
            context=self.future_context,
            connection_file=cf,
        )
        print(f"{manager.kernel_cmd}")
        print(f"{manager.kernel_spec}")
        manager.start_kernel()
        self.kernel = KernelProxy(manager=manager, shell_upstream=self.shell_stream)
        self.iosub.connect(self.kernel.iopub_url)
        return [self.kernel]

    def get_kernel(self, name):
        """Get a kernel, start it if it doesn't exist"""
        if self.kernel is None:
            self.start_kernel("atk")
        return self.kernel

    def set_parent(self, ident, parent):
        # record the parent message
        self._atk_parent = parent
        return super().set_parent(ident, parent)

    def split_cell(self, cell):
        """Return the kernel name and remaining cell contents

        If no kernel name is specified, use the default kernel.
        """
        if not cell.startswith('>'):
            # no kernel magic, use default kernel
            return self.default_kernel, cell
        split = cell.split('\n', 1)
        if len(split) == 2:
            first_line, cell = split
        else:
            first_line = cell
            cell = ''
        kernel_name = first_line[1:].strip()
        if kernel_name[0] == "!":
            # >!kernelname sets it as the new default
            kernel_name = kernel_name[1:].strip()
            self.default_kernel = kernel_name
        return kernel_name, cell

    def _publish_status(self, status):
        """Disabling publishing status messages for relayed

        Status messages will be relayed from the actual kernels.
        """
        if self._atk_parent and self._atk_parent['header']['msg_type'] in {
            'execute_request', 'inspect_request', 'complete_request'
        }:
            self.log.debug("suppressing %s status message.", status)
            return
        else:
            return super()._publish_status(status)

    def intercept_kernel(self, stream, ident, parent):

        content = parent["content"]
        cell = content["code"]
        if cell == "%restart":
            ## ask kernel to do nothing but still send an empty reply to flush ZMQ
            parent["content"]["code"] = ""
            parent["content"]["silent"] = True

        res = self.relay_to_kernel(stream, ident, parent)

        if cell == "%restart":

            self.kernel.manager.shutdown_kernel(now=False, restart=True)
            self.kernel = None

        return res

    def relay_to_kernel(self, stream, ident, parent):

        """Relay a message to a kernel

        Gets the `>kernel` line off of the cell,
        finds the kernel (starts it if necessary),
        then relays the request.
        """

        kernel_name = "atk"
        kernel = self.get_kernel(kernel_name)
        self.log.debug("Relaying %s to %s", parent["header"]["msg_type"], kernel_name)
        self.session.send(kernel.shell, parent, ident=ident)

    execute_request = intercept_kernel
    inspect_request = relay_to_kernel
    complete_request = relay_to_kernel

    def do_shutdown(self, restart):
        self.kernel.manager.shutdown_kernel(now=False, restart=restart)
        return super().do_shutdown(restart)


class RestarterApp(IPKernelApp):

    kernel_class = Proxy
    # disable IO capture
    outstream_class = None

    def _log_level_default(self):
        return 0


def main():
    atk = RestarterApp.launch_instance()


if __name__ == "__main__":
    print("Hello Hello")
    main()
    print("bye !")
