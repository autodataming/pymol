#!/usr/bin/env python
#
# This script only applies if you are performing a Python Distutils-based
# installation of PyMOL.
#
# It may assume that all of PyMOL's external dependencies are
# pre-installed into the system.

from distutils.core import setup, Extension
from distutils.util import change_root
from distutils.errors import *
from distutils.command.install import install
from distutils.command.build import build
from glob import glob
import shutil
import sys, os

import distutils.ccompiler
import multiprocessing.pool

class monkeypatch(object):
    '''Decorator for replacing a method in a class or module'''
    def __init__(self, cls, name=None):
        self.cls = cls
        self.name = name
    def __call__(self, func):
        name = self.name or func.__name__
        setattr(self.cls, name, func)
        return func

@monkeypatch(distutils.ccompiler.CCompiler, 'compile')
def parallel_compile(self, sources, output_dir=None, macros=None,
        include_dirs=None, debug=0, extra_preargs=None, extra_postargs=None,
        depends=None):
    '''
    http://stackoverflow.com/questions/11013851/speeding-up-build-process-with-distutils
    '''
    # those lines are copied from distutils.ccompiler.CCompiler directly
    macros, objects, extra_postargs, pp_opts, build = self._setup_compile(
            output_dir, macros, include_dirs, sources, depends, extra_postargs)
    cc_args = self._get_cc_args(pp_opts, debug, extra_preargs)

    # parallel code
    def _single_compile(obj):
        try:
            src, ext = build[obj]
        except KeyError:
            return
        self._compile(obj, src, ext, cc_args, extra_postargs, pp_opts)

    multiprocessing.pool.ThreadPool().map(_single_compile, objects)
    return objects

class build_pymol(build):
    vmd_plugins = 'crdplugin dcdplugin gromacsplugin'
    user_options = build.user_options + [
        ('vmd-plugins=', None, 'list of plugins to include'),
        ]

    def run(self):
        self.vmd_plugins_enable()
        build.run(self)

    def vmd_plugins_enable(self):
        name_list = self.vmd_plugins.split()
        if not name_list:
            return

        src_path = 'contrib/uiuc/plugins/molfile_plugin/src'
        inc_dirs = [src_path, 'contrib/uiuc/plugins/include']
        sources = [os.path.join(src_path, f) for f in ['PlugIOManagerInit2.c', 'hash.c']]

        for name in name_list:
            try:
                filename = glob(src_path + '/' + name + '.c*')[0]
            except LookupError:
                raise DistutilsOptionError('No such VMD plugin: ' + name)
            sources.append(filename)

        self.vmd_plugins_write_PIOMI(sources[0], name_list)
        self.vmd_plugins_register(sources, inc_dirs)

    def vmd_plugins_write_PIOMI(self, filename, name_list):
        g = open(filename, 'w')

        g.write('''
        /* MACHINE GENERATED FILE, DO NOT EDIT! */
        #include "vmdplugin.h"
        typedef struct _PyMOLGlobals PyMOLGlobals;
        int PlugIOManagerRegister(PyMOLGlobals *G, vmdplugin_t *);
        ''')

        # prototypes
        for name in name_list:
            g.write('''
            int molfile_%s_init(void);
            int molfile_%s_register(void *, vmdplugin_register_cb);
            int molfile_%s_fini(void);
            ''' % (name, name, name))

        g.write('''
        int PlugIOManagerInitAll(PyMOLGlobals *G) {
            return 1
        ''')
        for name in name_list:
            g.write('''
            && (molfile_%s_init() == VMDPLUGIN_SUCCESS)
            && (molfile_%s_register(G, (vmdplugin_register_cb)PlugIOManagerRegister) == VMDPLUGIN_SUCCESS)
            ''' % (name, name))
        g.write('''
            ;
        }

        int PlugIOManagerFreeAll(void) {
            return 1
        ''')
        for name in name_list:
            g.write('''
            && (molfile_%s_fini() == VMDPLUGIN_SUCCESS)
            ''' % name)
        g.write('''
            ;
        }
        ''')

        g.close()

    def vmd_plugins_register(self, sources, inc_dirs):
        for e in self.distribution.ext_modules:
            if e.name == 'pymol._cmd':
                e.sources += sources
                e.include_dirs += inc_dirs
                e.define_macros += [('_PYMOL_VMD_PLUGINS', None)]

class install_pymol(install):
    pymol_path = None
    user_options = install.user_options + [
        ('pymol-path=', None, 'PYMOL_PATH'),
        ]

    def finalize_options(self):
        install.finalize_options(self)
        if self.pymol_path is None:
            self.pymol_path = os.path.join(self.install_libbase, 'pymol', 'pymol_path')
        elif self.root is not None:
            self.pymol_path = change_root(self.root, self.pymol_path)

    def run(self):
        install.run(self)
        self.install_pymol_path()
        self.make_launch_script()

    def unchroot(self, name):
        if self.root is not None and name.startswith(self.root):
            return name[len(self.root):]
        return name

    def copy_tree_nosvn(self, src, dst):
        ignore = lambda src, names: set(['.svn']).intersection(names)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        print 'copying', src, '->', dst
        shutil.copytree(src, dst, ignore=ignore)

    def copy(self, src, dst):
        copy = self.copy_tree_nosvn if os.path.isdir(src) else self.copy_file
        copy(src, dst)

    def install_pymol_path(self):
        self.mkpath(self.pymol_path)
        for name in [ 'LICENSE', 'data', 'test', 'scripts', 'examples', ]:
            self.copy(name, os.path.join(self.pymol_path, name))

    def make_launch_script(self):
        if sys.platform.startswith('win'):
           launch_script = 'pymol.bat'
        else:
           launch_script = 'pymol'

        python_exe = os.path.abspath(sys.executable)
        pymol_file = self.unchroot(os.path.join(self.install_libbase, 'pymol', '__init__.py'))
        pymol_path = self.unchroot(self.pymol_path)

        with open(launch_script, 'w') as out:
            if sys.platform.startswith('win'):
                out.write('set PYMOL_PATH=' + pymol_path + os.linesep)
                out.write('"%s" "%s"' % (python_exe, pymol_file))
                out.write(' %1 %2 %3 %4 %5 %6 %7 %8 %9' + os.linesep)
            else:
                out.write('#!/bin/sh' + os.linesep)
                if sys.platform.startswith('darwin'):
                    out.write('if [ "$DISPLAY" == "" ]; then DISPLAY=":0.0"; export DISPLAY; fi' + os.linesep)
                out.write('PYMOL_PATH="%s"; export PYMOL_PATH' % pymol_path + os.linesep)
                out.write('"%s" "%s" "$@"' % (python_exe, pymol_file) + os.linesep)

        os.chmod(launch_script, 0755)
        self.mkpath(self.install_scripts)
        self.copy(launch_script, self.install_scripts)


#============================================================================
if sys.platform=='win32': 
    # NOTE: this branch not tested in years and may not work...
    inc_dirs=["ov/src",
              "layer0","layer1","layer2",
              "layer3","layer4","layer5",
              "win32/include"]
    libs=["opengl32","glu32","glut32","libpng","zlib"]
    pyogl_libs = ["opengl32","glu32","glut32"]
    lib_dirs=["win32/lib"]
    def_macros=[("_PYMOL_MODULE",None),
                ("WIN32",None),
                ("_PYMOL_LIBPNG",None),
                ]
    ext_comp_args=[]
    ext_link_args=['/NODEFAULTLIB:"LIBC"']
#============================================================================
elif sys.platform=='cygwin':
    # NOTE: this branch not tested in years and may not work...
    inc_dirs=["ov/src",
              "layer0","layer1","layer2",
              "layer3","layer4","layer5",
	      "modules/cealign/src", 
	      "modules/cealign/src/tnt" ]
    libs=["glut32","opengl32","glu32","png"]
    pyogl_libs = ["glut32","opengl32","glu32"]
    lib_dirs=["/usr/lib/w32api"]
    def_macros=[("_PYMOL_MODULE",None),
                ("CYGWIN",None),
                ("_PYMOL_LIBPNG",None)]
    ext_comp_args=[]
    ext_link_args=[]
#============================================================================
elif sys.platform=='darwin':
    using_fink = "'/sw/" in str(sys.path)
    if using_fink:
        # under Fink, with the following packages installed:
        #
        #  python27
        #  libpng15
        #  pmw-py27
        #  freetype219
        #  freeglut
        #  glew
        #
        # REMEMBER to use Fink's Python!
        #
        try:
            os.makedirs("generated/include")
        except OSError:
            # ignore error if directory already exists
            pass

        try:
            os.makedirs("generated/src")
        except OSError:
            # ignore error if directory already exists
            pass

        import create_shadertext

        outputheader = open("generated/include/ShaderText.h",'w')
        outputfile = open("generated/src/ShaderText.c",'w')

        create_shadertext.create_shadertext("data/shaders",
                                            "shadertext.txt",
                                            outputheader,
                                            outputfile)

        outputheader.close()
        outputfile.close()

        inc_dirs=["ov/src",
                  "layer0","layer1","layer2",
                  "layer3","layer4","layer5", 
                  "/sw/include/freetype2/freetype",
                  "/sw/include/freetype2",
                  "/sw/include",
                  "/usr/X11/include",
		  "modules/cealign/src",
		  "modules/cealign/src/tnt",
		  #"contrib/uiuc/plugins/include/",
		  #"contrib/uiuc/plugins/molfile_plugin/src",
                  "generated/include",
                  "generated/src",
                  ]
        libs=[]
        pyogl_libs = []
        lib_dirs=[]
        def_macros=[("_PYMOL_MODULE",None),
                    ("_PYMOL_LIBPNG",None),
                    ("_PYMOL_FREETYPE",None),
                    ("_PYMOL_INLINE",None),
                    ("_PYMOL_NUMPY",None),
                    ("_PYMOL_OPENGL_SHADERS",None),
                    ("NO_MMLIBS",None),
                    ("_PYMOL_CGO_DRAWARRAYS",None),
                    ("_PYMOL_CGO_DRAWBUFFERS",None),
                    ("_CGO_DRAWARRAYS",None),
                    ("_PYMOL_GL_CALLLISTS",None),
                    ("OPENGL_ES_2",None),
                    ]
        ext_comp_args=[]
        ext_link_args=[
                       "-L/sw/lib", "-lpng",
                       "/usr/X11/lib/libGL.dylib",
                       "/usr/X11/lib/libGLU.dylib",
                       "-lfreeglut",
                       "-lglew",
                       "-L/sw/lib/freetype219/lib", "-lfreetype"
                        ]
    else:
        # Not using Fink -- building as if we are on Linux/X11 with
        # the external dependencies compiled into "./ext" in the
        # current working directory,
        #
        # REMEMEBER to use "./ext/bin/python ..."
        #
        # create shader text
        try:
            os.makedirs("generated/include")
        except OSError:
            # ignore error if directory already exists
            pass

        try:
            os.makedirs("generated/src")
        except OSError:
            # ignore error if directory already exists
            pass

        import create_shadertext

        outputheader = open("generated/include/ShaderText.h",'w')
        outputfile = open("generated/src/ShaderText.c",'w')

        create_shadertext.create_shadertext("data/shaders",
                                            "shadertext.txt",
                                            outputheader,
                                            outputfile)

        outputheader.close()
        outputfile.close()

        EXT = "/opt/local"
        inc_dirs=["ov/src",
                  "layer0","layer1","layer2",
                  "layer3","layer4","layer5", 
                  EXT+"/include",
                  EXT+"/include/GL",
                  EXT+"/include/freetype2",
		  "modules/cealign/src",
		  "modules/cealign/src/tnt",
                  "generated/include",
                  "generated/src",
                  ]
        libs=[]
        pyogl_libs = []
        lib_dirs=[]
        def_macros=[("_PYMOL_MODULE",None),
                    ("_PYMOL_LIBPNG",None),
                    ("_PYMOL_FREETYPE",None),
                    ("_PYMOL_INLINE",None),
                    ("_PYMOL_NUMPY",None),
                    ("_PYMOL_OPENGL_SHADERS",None),
                    ("NO_MMLIBS",None),
                    ("_PYMOL_CGO_DRAWARRAYS",None),
                    ("_PYMOL_CGO_DRAWBUFFERS",None),
                    ("_CGO_DRAWARRAYS",None),
                    ("_PYMOL_GL_CALLLISTS",None),
                    ("OPENGL_ES_2",None),
                    ]
        ext_comp_args=["-ffast-math","-funroll-loops","-O3","-fcommon"]
        ext_link_args=[
                    "-L"+EXT+"/lib", "-lpng", "-lGL", "-lglut", "-lGLEW", "-lfreetype"
            ]
#============================================================================
else: # linux or other unix

    # create shader text
    try:
        os.makedirs("generated/include")
    except OSError:
        # ignore error if directory already exists
        pass

    try:
        os.makedirs("generated/src")
    except OSError:
        # ignore error if directory already exists
        pass

    import create_shadertext

    outputheader = open("generated/include/ShaderText.h",'w')
    outputfile = open("generated/src/ShaderText.c",'w')

    create_shadertext.create_shadertext("data/shaders", 
                                        "shadertext.txt", 
                                        outputheader, 
                                        outputfile)

    outputheader.close()
    outputfile.close()

    inc_dirs = [ "ov/src",
                 "layer0",
                 "layer1",
                 "layer2",
                 "layer3",
                 "layer4",
                 "layer5",
                 "/usr/include/freetype2",
              # VMD plugin support
              #              "contrib/uiuc/plugins/include",
              #              "contrib/uiuc/plugins/molfile_plugin/src",
                 "modules/cealign/src",
                 "modules/cealign/src/tnt",
                 "generated/include",
                 "generated/src", ]
    libs = [ "GL",
             "GLU",
             "glut",
             "png",
             "z",
             "freetype",
             "GLEW",
             # "Xxf86vm"
          ]	
    pyogl_libs = [ "GL", 
                   "GLU",
                   "glut",
                   "GLEW"]
    lib_dirs = [ "/usr/X11R6/lib64", ]
    def_macros = [ ("_PYMOL_MODULE",None),
                   ("_PYMOL_INLINE",None),
                   ("_PYMOL_FREETYPE",None),
                   ("_PYMOL_LIBPNG",None),
                   # OpenGL shaders
                   ("_PYMOL_OPENGL_SHADERS",None),
                   # Numeric Python support                    
                   #                ("_PYMOL_NUMPY",None),
                   # VMD plugin support           
                   #               ("_PYMOL_VMD_PLUGINS",None)
                   ("_PYMOL_CGO_DRAWARRAYS",None),
                   ("_PYMOL_CGO_DRAWBUFFERS",None),
                   ("_CGO_DRAWARRAYS",None),
                   ("_PYMOL_GL_CALLLISTS",None),
                   ("OPENGL_ES_2",None),
                   ]
    ext_comp_args = [ "-ffast-math",
                      "-funroll-loops",
                      "-O3",
                      "-g" ]
    ext_link_args = []


distribution = setup ( # Distribution meta-data
    cmdclass  = {'build': build_pymol, 'install': install_pymol},
    name      = "pymol",
    version   = "1.5.0.3", # see layer0/Version.h for updated version
    author    = "Schrodinger",
    url       = "http://pymol.org",
    contact   = "pymol-users@lists.sourceforge.net",
    description = "PyMOL is a Python-enhanced molecular graphics tool. It excels at 3D visualization of proteins, small molecules, density, surfaces, and trajectories. It also includes molecular editing, ray tracing, and movies. Open Source PyMOL is free to everyone!", 
    package_dir = {'' : 'modules'},
    packages = [ 'chempy',
                 'chempy/bmin',
                 'chempy/champ',
                 'chempy/fast',
                 'chempy/fragments',
                 'chempy/tinker',
                 'pmg_tk',
                 'pmg_tk/startup',
                 'pmg_tk/skins',
                 'pmg_tk/skins/normal',                    
                 'pmg_wx',
                 'pymol',
                 'pymol/contrib',                
                 'pymol/opengl',
                 'pymol/opengl/gl',
                 'pymol/opengl/glu',
                 'pymol/opengl/glut',
                 'pymol/wizard',
                 'pymol/plugins',
                 'pymol2',
                 'web',
                 'web/examples',
                 'web/javascript', ],
    ext_modules = [
        Extension("pymol._cmd", [
                "modules/cealign/src/ccealignmodule.cpp",
                "generated/src/ShaderText.c",
                "ov/src/OVContext.c",
                "ov/src/OVHeapArray.c",
                "ov/src/OVHeap.c",
                "ov/src/OVLexicon.c",
                "ov/src/OVOneToOne.c",
                "ov/src/OVOneToAny.c",
                "ov/src/OVRandom.c",
                "ov/src/ov_utility.c",
                "layer0/Block.c",
                "layer0/Crystal.c",
                "layer0/Debug.c",
                "layer0/Deferred.c",
                "layer0/Err.c",
                "layer0/Feedback.c",
                "layer0/Field.c",
                "layer0/Isosurf.c",
                "layer0/Map.c",
                "layer0/Match.c",
                "layer0/Matrix.c",
                "layer0/MemoryDebug.c",
                "layer0/MemoryCache.c",
                "layer0/MyPNG.c",
                "layer0/Parse.c",
                "layer0/Pixmap.c",
                "layer0/Queue.c",
                "layer0/Raw.c",
                "layer0/Sphere.c",
                "layer0/ShaderMgr.c",
                "layer0/Tetsurf.c",
                "layer0/Texture.c",
                "layer0/Tracker.c",
                "layer0/Triangle.c",
                "layer0/Util.c",
                "layer0/Vector.c",
                "layer0/Word.c",
                "layer0/os_gl.c",
                "layer1/Basis.c",
                "layer1/ButMode.c",
                "layer1/Character.c",
                "layer1/CGO.c",
                "layer1/Color.c",
                "layer1/Control.c",
                "layer1/Extrude.c",
                "layer1/Font.c",
                "layer1/FontType.c",
                "layer1/FontGLUT.c",
                "layer1/FontGLUT8x13.c",
                "layer1/FontGLUT9x15.c",
                "layer1/FontGLUTHel10.c",
                "layer1/FontGLUTHel12.c",
                "layer1/FontGLUTHel18.c",
                "layer1/Movie.c",
                "layer1/Ortho.c",
                "layer1/P.c",
                "layer1/PConv.c",
                "layer1/Pop.c",
                "layer1/PyMOLObject.c",
                "layer1/Ray.c",
                "layer1/Rep.c",
                "layer1/Scene.c",
                "layer1/ScrollBar.c",
                "layer1/Seq.c",
                "layer1/Setting.c",
                "layer1/Shaker.c",
                "layer1/Symmetry.c",
                "layer1/Text.c",
                "layer1/TypeFace.c",
                "layer1/Wizard.c",
                "layer1/View.c",
                "layer2/AtomInfo.c",
                "layer2/CoordSet.c",
                "layer2/GadgetSet.c",    
                "layer2/DistSet.c",
                "layer2/ObjectAlignment.c",
                "layer2/ObjectCGO.c",
                "layer2/ObjectCallback.c",
                "layer2/ObjectDist.c",
                "layer2/ObjectMap.c",
                "layer2/ObjectMesh.c",
                "layer2/ObjectMolecule.c",
                "layer2/ObjectMolecule2.c",
                "layer2/ObjectSurface.c",
                "layer2/ObjectSlice.c",
                "layer2/ObjectVolume.c",
                "layer2/RepCartoon.c",
                "layer2/RepCylBond.c",
                "layer2/RepDistDash.c",
                "layer2/RepDistLabel.c",
                "layer2/RepDot.c",
                "layer2/RepLabel.c",
                "layer2/RepMesh.c",
                "layer2/ObjectGadget.c",
                "layer2/ObjectGadgetRamp.c",
                "layer2/ObjectGroup.c",
                "layer2/RepAngle.c",        
                "layer2/RepDihedral.c",    
                "layer2/RepNonbonded.c",
                "layer2/RepNonbondedSphere.c",
                "layer2/RepRibbon.c",
                "layer2/RepSphere.c",
                "layer2/RepEllipsoid.c",    
                "layer2/RepSurface.c",
                "layer2/RepWireBond.c",
                "layer2/Sculpt.c",
                "layer2/SculptCache.c",
                "layer2/VFont.c",    
                "layer3/PlugIOManager.c",
                "layer3/Editor.c",
                "layer3/Executive.c",
                "layer3/Seeker.c",
                "layer3/Selector.c",
                "layer4/Cmd.c",
                "layer4/Export.c",
                "layer4/Menu.c",
                "layer4/PopUp.c",
                "layer5/PyMOL.c",
                "layer5/TestPyMOL.c",
                "layer5/main.c"
                # VMD plugin support
                # switch the 0 to 1 to activate the additional source code
                ] + 0 * [
                # (incomplete support -- only TRJ, TRR, XTC, DCD so far...)
                'contrib/uiuc/plugins/molfile_plugin/src/PlugIOManagerInit.c',
                'contrib/uiuc/plugins/molfile_plugin/src/avsplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/basissetplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/bgfplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/binposplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/biomoccaplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/brixplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/carplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/ccp4plugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/corplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/cpmdlogplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/cpmdplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/crdplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/cubeplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/dcdplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/dlpolyplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/dsn6plugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/dtrplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/dxplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/edmplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/fs4plugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/gamessplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/gaussianplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/graspplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/grdplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/gridplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/gromacsplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/hash.c',
                'contrib/uiuc/plugins/molfile_plugin/src/jsplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/maeffplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/mapplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/mdfplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/mmcif.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/mol2plugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/moldenplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/mrcplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/msmsplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/namdbinplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/parm7plugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/parmplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/pbeqplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/pdbplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/phiplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/pltplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/pqrplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/psfplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/raster3dplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/rst7plugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/situsplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/spiderplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/stlplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/tinkerplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/uhbdplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspchgcarplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspoutcarplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspparchgplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspposcarplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspxdatcarplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vaspxmlplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/vtfplugin.c',
                'contrib/uiuc/plugins/molfile_plugin/src/xbgfplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/xsfplugin.cpp',
                'contrib/uiuc/plugins/molfile_plugin/src/xyzplugin.c'    
                ],
                  include_dirs = inc_dirs,
                  libraries = libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),        
        Extension("chempy.champ._champ", [
                "contrib/champ/champ.c",
                "contrib/champ/champ_module.c",
                "contrib/champ/chiral.c",
                "contrib/champ/err2.c",
                "contrib/champ/feedback2.c",
                "contrib/champ/list.c",
                "contrib/champ/os_memory.c",
                "contrib/champ/sort.c",
                "contrib/champ/strblock.c",
                "contrib/champ/vla.c",
                ],
                  include_dirs=["contrib/champ"]
                  ),
        Extension("pymol.opengl.glu._glu_num", ["contrib/pyopengl/_glu_nummodule.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.glu._glu", ["contrib/pyopengl/_glumodule.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.glut._glut", ["contrib/pyopengl/_glutmodule.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.gl._opengl_num", ["contrib/pyopengl/_opengl_nummodule.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.gl._opengl", ["contrib/pyopengl/_openglmodule.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.gl.openglutil", ["contrib/pyopengl/openglutil.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  ),
        Extension("pymol.opengl.gl.openglutil_num", ["contrib/pyopengl/openglutil_num.c"],
                  include_dirs = inc_dirs,
                  libraries = pyogl_libs,
                  library_dirs = lib_dirs,
                  define_macros = def_macros,
                  extra_link_args = ext_link_args,
                  extra_compile_args = ext_comp_args,
                  )
        ])
