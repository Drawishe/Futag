"""
**************************************************
**      ______  __  __  ______  ___     ______  **
**     / ____/ / / / / /_  __/ /   |   / ____/  **
**    / /_    / / / /   / /   / /| |  / / __    **
**   / __/   / /_/ /   / /   / ___ | / /_/ /    **
**  /_/      \____/   /_/   /_/  |_| \____/     **
**                                              **
**     Fuzzing target Automated Generator       **
**             a tool of ISP RAS                **
**************************************************
** This module is for generating, compiling     **
** fuzz-drivers of functions in library         **
**************************************************
"""

import json
import pathlib
import copy
from futag.sysmsg import *
from futag.preprocessor import *

from subprocess import Popen, PIPE
from multiprocessing import Pool
from typing import List
from distutils.dir_util import copy_tree


class Generator:
    """Futag Generator"""

    def __init__(self, futag_llvm_package: str, library_root: str, target_type: int = LIBFUZZER, json_file: str = ANALYSIS_FILE_PATH, output_path=FUZZ_DRIVER_PATH, build_path=BUILD_PATH, install_path=INSTALL_PATH):
        """
        Parameters
        ----------
        futag_llvm_package: str
            path to the futag llvm package (with binaries, scripts, etc)
        library_root: str
            path to the library root
        target_type: int
            format of fuzz-drivers (LIBFUZZER or AFLPLUSPLUS), default to LIBFUZZER 
        json_file: str
            path to the futag-analysis-result.json file
        output_path : str
            where to save fuzz-drivers, if this path exists, Futag will delete it and create new one, default "futag-fuzz-drivers"
        build_path: str
            path to the build directory.
        install_path: str
            path to the install directory.
        """

        self.output_path = None  # Path for saving fuzzing drivers
        self.tmp_output_path  = None  # Path for saving fuzzing drivers
        self.json_file = json_file
        self.futag_llvm_package = futag_llvm_package
        self.library_root = library_root
        self.target_library = None

        self.gen_func_params = []
        self.gen_free = []
        self.gen_this_function = True
        self.buf_size_arr = []
        self.dyn_size = 0
        self.curr_gen_string = -1
        # save the list of generated function for debugging
        self.target_extension = ""
        self.result_report = {}

        if (target_type > 1 or target_type < 0):
            raise ValueError(INVALID_TARGET_TYPE)

        self.target_type = target_type

        if pathlib.Path(self.futag_llvm_package).exists():
            self.futag_llvm_package = pathlib.Path(
                self.futag_llvm_package).absolute()
        else:
            raise ValueError(INVALID_FUTAG_PATH)

        if pathlib.Path(self.library_root).exists():
            self.library_root = pathlib.Path(self.library_root).absolute()
        else:
            raise ValueError(INVALID_LIBPATH)

        if not pathlib.Path(json_file).exists():
            self.json_file = self.library_root / ANALYSIS_FILE_PATH
        else:
            self.json_file = pathlib.Path(json_file)

        if self.json_file.exists():
            f = open(self.json_file.as_posix())
            if not f.closed:
                self.target_library = json.load(f)
            tmp_output_path = "." + output_path
            # create directory for function targets if not exists
            # TODO: set option for deleting
            if (self.library_root / output_path).exists():
                delete_folder(self.library_root / output_path)
            if (self.library_root / tmp_output_path).exists():
                delete_folder(self.library_root / tmp_output_path)

            (self.library_root / output_path).mkdir(parents=True, exist_ok=True)
            (self.library_root / tmp_output_path).mkdir(parents=True, exist_ok=True)
            self.output_path = self.library_root / output_path
            self.tmp_output_path = self.library_root / tmp_output_path
            
        else:
            raise ValueError(INVALID_ANALYSIS_FILE)

        if not (self.library_root / build_path).exists():
            raise ValueError(INVALID_BUILPATH)
        self.build_path = self.library_root / build_path

        if not (self.library_root / install_path).exists():
            raise ValueError(INVALID_INSTALLPATH)
        self.install_path = self.library_root / install_path
        self.var_function = 0
        self.var_files = 0

    def gen_header(self, target_function_fname):
        defaults = ["stdio.h", "stddef.h", "stdlib.h", "string.h", "stdint.h"]
        compiled_files = self.target_library["compiled_files"]
        included_headers = []
        found = False
        for f in compiled_files:
            if f["filename"] == target_function_fname:
                found = True
                for header in f["headers"]:
                    if not header[1:-1] in defaults:
                        included_headers.append(header)
                break
        if not found:
            # print (target_function_fname, " not found!")
            short_filename = target_function_fname.split('/')[-1]
            # print("short filename:", short_filename)
            for f in compiled_files:
                # print("short compiled_files:", f["filename"].split('/')[-1])
                if f["filename"].split('/')[-1] == short_filename:
                    found = True
                    for header in f["headers"]:
                        if not header[1:-1] in defaults:
                            included_headers.append(header)
                    break
        include_lines = []
        for i in defaults:
            include_lines.append("#include <" + i + ">\n")
        for i in included_headers:
            include_lines.append("#include " + i + "\n")
        return include_lines

    def gen_builtin(self, type_name, var_name):

        return {
            "gen_lines": [
                "//GEN_BUILTIN\n",
                type_name + " " + var_name + ";\n",
                "memcpy(&"+var_name+", pos, sizeof(" + type_name + "));\n",
                "pos += sizeof(" + type_name + ");\n"
            ],
            "gen_free": []
        }

    def gen_size(self, type_name, var_name):
        return {
            "gen_lines": [
                "//GEN_SIZE\n",
                type_name + " " + var_name +
                " = (" + type_name + ") dyn_size;\n",
            ],
            "gen_free": []
        }

    def gen_string(self, type_name, var_name, parent_type):
        if (len(parent_type) > 0):
            return {
                "gen_lines": [
                    "//GEN_STRING\n",
                    parent_type + " r" + var_name + " = (" + parent_type + ") " +
                    "malloc(sizeof(char) * dyn_size + 1);\n",
                    "memset(r" + var_name+", 0, sizeof(char) * dyn_size + 1);\n",
                    "memcpy(r" + var_name+", pos, sizeof(char) * dyn_size );\n",
                    "pos += sizeof(char) * dyn_size ;\n",
                    type_name + " " + var_name + "= r" + var_name + ";\n"
                ],
                "gen_free": [
                    # "if (dyn_size > 0 && strlen(r" + var_name + \
                    # ") > 0) {\n",
                    "if (r" + var_name + ") {\n",
                    "    free(r" + var_name + ");\n",
                    "    r" + var_name + " = NULL;\n",
                    "}\n"
                ]
            }
        return {
            "gen_lines": [
                "//GEN_STRING\n",
                type_name + " " + var_name + " = (" + type_name + ") " +
                "malloc(sizeof(char) * dyn_size + 1);\n",
                "memset(" + var_name+", 0, sizeof(char) * dyn_size + 1);\n",
                "memcpy(" + var_name+", pos, sizeof(char) * dyn_size );\n",
                "pos += sizeof(char) * dyn_size ;\n"
            ],
            "gen_free": [
                # "if (dyn_size > 0 && strlen(" + var_name + \
                # ") > 0) {\n",
                "if (" + var_name + ") {\n",
                "    free( " + var_name + ");\n",
                "    " + var_name + " = NULL;\n",
                "}\n"
            ]
        }

    def gen_enum(self, type_name, var_name):
        # Foo foo = static_cast<Foo>(fooInt)
        return {
            "gen_lines": [
                "//GEN_ENUM\n"
            ],
            "gen_free": []
        }

    def gen_array(self, type_name, var_name):
        return {
            "gen_lines": [
                "//GEN_ARRAY\n"
            ],
            "gen_free": []
        }

    def gen_void(self, var_name):
        return {
            "gen_lines": [
                "//GEN_VOID\n"
            ],
            "gen_free": []
        }

    def gen_qualifier(self, type_name, var_name, parent_type, parent_gen, param_id):

        if parent_type in ["const char *", "const unsigned char *"]:
            self.dyn_size += 1
            temp_type = parent_type[6:]
            self.curr_gen_string = param_id
            return {
                "gen_lines": [
                    "//GEN_STRING\n",
                    temp_type + " s" + var_name + " = (" + temp_type + ") " +
                    "malloc(sizeof(char) * dyn_size + 1);\n",
                    "memset(s" + var_name+", 0, sizeof(char) * dyn_size + 1);\n",
                    "memcpy(s" + var_name+", pos, sizeof(char) * dyn_size );\n",
                    "pos += sizeof(char) * dyn_size ;\n",
                    parent_type + " u" + var_name + "= s" + var_name + ";\n",
                    "//GEN_QUALIFIED\n",
                    type_name + " " + var_name + " = u" + var_name + ";\n",
                ],
                "gen_free": [
                    "if (s" + var_name + ") {\n",
                    "    free(s" + var_name + ");\n",
                    "    s" + var_name + " = NULL;\n",
                    "}\n"
                ],
                "buf_size": "sizeof(char)",
            }
        if parent_type in ["char *", "unsigned char *"]:
            self.dyn_size += 1
            self.curr_gen_string = param_id
            return {
                "gen_lines": [
                    "//GEN_STRING\n",
                    parent_type + " s" + var_name + " = (" + parent_type + ") " +
                    "malloc(sizeof(char) * dyn_size + 1);\n",
                    "memset(s" + var_name+", 0, sizeof(char) * dyn_size + 1);\n",
                    "memcpy(s" + var_name+", pos, sizeof(char) * dyn_size );\n",
                    "pos += sizeof(char) * dyn_size ;\n",
                    "//GEN_QUALIFIED\n",
                    type_name + " " + var_name + " = s" + var_name + ";\n"
                ],
                "gen_free": [
                    "if (s" + var_name + " ) {\n",
                    "    free(s" + var_name + ");\n",
                    "    s" + var_name + " = NULL;\n",
                    "}\n"
                ],
                "buf_size": "sizeof(char)"
            }
        if parent_gen == "incomplete":
            self.gen_this_function = False
            return {
                "gen_lines": [
                    "//GEN_VOID\n"
                ],
                "gen_free": [],
                "buf_size": ""
            }
        return {
            "gen_lines": [
                "//GEN_QUALIFIED\n",
                parent_type + " u" + var_name + ";\n",
                "memcpy(&u"+var_name+", pos, sizeof(" + parent_type + "));\n",
                "pos += sizeof(" + parent_type + ");\n",
                type_name + " " + var_name + " = u" + var_name + ";\n"
            ],
            "gen_free": [],
            "buf_size": ""
        }

    def gen_pointer(self, type_name, var_name, parent_type):
        return {
            "gen_lines": [
                "//GEN_POINTER\n",
                parent_type + " r" + var_name + ";\n",
                "memcpy(&r" + var_name + ", pos, sizeof(" + parent_type + "));\n",
                "pos += sizeof(" + parent_type + ");\n",
                type_name + " " + var_name + "= &r" + var_name + ";\n"
            ],
            "gen_free": []
        }

    def gen_struct(self, type_name, var_name):
        return {
            "gen_lines": [
                "//GEN_STRUCT\n"
            ],
            "gen_free": []
        }

    def gen_input_file(self, var_name):
        cur_gen_free = ["    " + x for x in self.gen_free]
        gen_lines = [
            "//GEN_INPUT_FILE\n",
            "const char* " + var_name + " = \"futag_input_file\";\n",
            "FILE *fp"+str(self.var_files) +
            " = fopen(" + var_name + ",\"w\");\n",
            "if (fp"+str(self.var_files)+"  == NULL) {\n",
        ] + cur_gen_free + ["    return 0;\n",
                            "}\n",
                            "fwrite(pos, 1, dyn_size, fp" +
                            str(self.var_files)+");\n",
                            "fclose(fp"+str(self.var_files)+");\n",
                            "pos += dyn_size;\n"
                            ]
        return {
            "gen_lines": gen_lines,
            "gen_free": []
        }

    def check_gen_function(self, function):
        """ Check if we can initialize argument as function call """
        return True

    def gen_var_function(self, parent_func, func, var_name):
        """ Initialize for argument of function call """
        curr_gen_func_params = []
        curr_gen_free = []
        curr_buf_size_arr = []
        curr_dyn_size = 0
        param_list = []
        param_id = 0
        curr_gen_string = -1
        for arg in func["params"]:
            param_list.append("f"+str(self.var_function) +
                              "_" + arg["param_name"])
            if arg["generator_type"] == GEN_BUILTIN:
                if arg["param_type"].split(" ")[0] in ["volatile", "const"]:
                    if arg["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                        if curr_gen_string >= 0:
                            var_curr_gen = {
                                "gen_lines": [
                                    "//GEN_SIZE\n",
                                    arg["param_type"].split(" ")[1] + " uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + " = (" + arg["param_type"].split(" ")[1] + ") dyn_size;\n",
                                    arg["param_type"] + " f"+str(self.var_function)+"_" + arg["param_name"] + " = uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + ";\n"
                                ],
                            }
                        else:
                            var_curr_gen = {
                                "gen_lines": [
                                    "//GEN_BUILTIN\n",
                                    arg["param_type"].split(
                                        " ")[1] + " uf"+str(self.var_function)+"_" + arg["param_name"] + ";\n",
                                    "memcpy(&u" + arg["param_name"]+", pos, sizeof(" +
                                    arg["param_type"].split(" ")[1] + "));\n",
                                    "pos += sizeof(" +
                                    arg["param_type"].split(" ")[1] + ");\n",
                                    arg["param_type"] + " f"+str(self.var_function)+"_" + arg["param_name"] + " = uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + ";\n"
                                ],
                            }
                            curr_buf_size_arr.append(
                                "sizeof(" + arg["param_type"].split(" ")[1]+")")
                    else:
                        if curr_gen_string == param_id - 1 and curr_gen_string >= 0:
                            var_curr_gen = {
                                "gen_lines": [
                                    "//GEN_SIZE\n",
                                    arg["param_type"].split(" ")[1] + " uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + " = (" + arg["param_type"].split(" ")[1] + ") dyn_size;\n",
                                    arg["param_type"] + " f"+str(self.var_function)+"_" + arg["param_name"] + " = uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + ";\n"
                                ],
                            }
                        else:
                            var_curr_gen = {
                                "gen_lines": [
                                    "//GEN_BUILTIN var\n",
                                    arg["param_type"].split(
                                        " ")[1] + " uf"+str(self.var_function)+"_" + arg["param_name"] + ";\n",
                                    "memcpy(&u" + arg["param_name"]+", pos, sizeof(" +
                                    arg["param_type"].split(" ")[1] + "));\n",
                                    "pos += sizeof(" +
                                    arg["param_type"].split(" ")[1] + ");\n",
                                    arg["param_type"] + " f"+str(self.var_function)+"_" + arg["param_name"] + " = uf"+str(
                                        self.var_function)+"_" + arg["param_name"] + ";\n"
                                ],
                            }
                            curr_buf_size_arr.append(
                                "sizeof(" + arg["param_type"].split(" ")[1]+")")
                else:
                    if arg["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                        if curr_gen_string >= 0:
                            var_curr_gen = self.gen_size(
                                arg["param_type"], "f"+str(self.var_function)+"_" + arg["param_name"])
                        else:
                            var_curr_gen = self.gen_builtin(
                                arg["param_type"], "f"+str(self.var_function)+"_" + arg["param_name"])
                            curr_buf_size_arr.append(
                                "sizeof(" + arg["param_type"]+")")
                    else:
                        if curr_gen_string == param_id - 1 and curr_gen_string >= 0:
                            var_curr_gen = self.gen_size(
                                arg["param_type"], "f"+str(self.var_function)+"_" + arg["param_name"])
                        else:
                            var_curr_gen = self.gen_builtin(
                                arg["param_type"], "f"+str(self.var_function)+"_" + arg["param_name"])
                            curr_buf_size_arr.append(
                                "sizeof(" + arg["param_type"]+")")
                curr_gen_func_params += var_curr_gen["gen_lines"]

            if arg["generator_type"] == GEN_STRING:
                if (arg["param_usage"] == "FILE_PATH" or arg["param_usage"] == "FILE_PATH_READ" or arg["param_usage"] == "FILE_PATH_WRITE" or arg["param_usage"] == "FILE_PATH_RW"):
                    var_curr_gen = self.gen_input_file(
                        "f"+str(self.var_function)+"_" + arg["param_name"])
                    self.var_files += 1
                    curr_dyn_size += 1
                    if not var_curr_gen:
                        return None

                    curr_gen_func_params += var_curr_gen["gen_lines"]

                else:
                    var_curr_gen = self.gen_string(
                        arg["param_type"],
                        "f"+str(self.var_function)+"_" + arg["param_name"],
                        arg["parent_type"])
                    curr_dyn_size += 1
                    if not var_curr_gen:
                        return None
                    curr_gen_func_params += var_curr_gen["gen_lines"]
                    curr_gen_free += var_curr_gen["gen_free"]
                    curr_gen_string = param_id
            param_id += 1

        function_call = "//GEN_VAR_FUNCTION\n    " + func["return_type"] + " " + var_name + \
            " = " + func["name"] + \
            "(" + ",".join(param_list)+");\n"

        # !attempting free on address which was not malloc()-ed
        #
        # if func["return_type_pointer"]:
        #     if func["return_type"].split(" ")[0] != "const" and not parent_func["return_type_pointer"]:
        #         curr_gen_free += ["if(" + var_name+ ") free("+var_name+");\n"]

        curr_gen_func_params.append(function_call)
        return {
            "gen_lines": curr_gen_func_params,
            "gen_free": curr_gen_free,
            "dyn_size": curr_dyn_size,
            "buf_size_arr": curr_buf_size_arr,
        }

    def wrapper_file(self, func):
        self.target_extension = func["location"].split(":")[-2].split(".")[-1]
        file_index = 1
        dir_name = func["qname"] + str(file_index)

        if not (self.tmp_output_path / func["qname"]).exists():
            (self.tmp_output_path / func["qname"]
             ).mkdir(parents=True, exist_ok=True)

        # Each variant of fuzz-driver will be save in separated directory
        # inside the directory of function

        while (self.tmp_output_path / func["qname"] / dir_name).exists():
            file_index += 1
            dir_name = func["qname"] + str(file_index)

        (self.tmp_output_path / func["qname"] /
         dir_name).mkdir(parents=True, exist_ok=True)

        file_name = func["qname"] + \
            str(file_index) + "." + self.target_extension

        full_path = (self.tmp_output_path /
                     func["qname"] / dir_name / file_name).as_posix()
        f = open(full_path, 'w')
        if f.closed:
            return None
        return f

    def gen_target_function(self, func, param_id) -> bool:
        malloc_free = [
            "unsigned char *",
            "char *",
        ]

        if param_id == len(func['params']):
            if not self.gen_this_function:
                return False
            # If there is no buffer - return!
            if not self.buf_size_arr:
                return False
            # generate file name
            f = self.wrapper_file(func)
            if not f:
                return False
            for line in self.gen_header(func["location"].split(':')[0]):
                f.write(line)
            f.write('\n')
            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_PREFIX)
            else:
                f.write(AFLPLUSPLUS_PREFIX)
            if self.dyn_size > 0:
                f.write("    if (Fuzz_Size < " + str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write(") return 0;\n")
                f.write(
                    "    size_t dyn_size = (int) ((Fuzz_Size - (" +
                    str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write("))/" + str(self.dyn_size) + ");\n")
            else:
                if len(self.buf_size_arr) > 0:
                    f.write("    if (Fuzz_Size < ")
                    f.write("+".join(self.buf_size_arr))
                    f.write(") return 0;\n")

            f.write("    uint8_t * pos = Fuzz_Data;\n")
            for line in self.gen_func_params:
                f.write("    " + line)

            f.write("    //FUNCTION_CALL\n")
            if func["return_type"] in malloc_free:
                f.write("    " + func["return_type"] +
                        " futag_target = " + func["qname"] + "(")
            else:
                f.write("    " + func["qname"] + "(")

            param_list = []
            for arg in func["params"]:
                param_list.append(arg["param_name"] + " ")
            f.write(",".join(param_list))
            f.write(");\n")

            # !attempting free on address which was not malloc()-ed

            if func["return_type"] in malloc_free:
                f.write("    if(futag_target){\n")
                f.write("        free(futag_target);\n")
                f.write("        futag_target = NULL;\n")
                f.write("    }\n")

            f.write("    //FREE\n")
            for line in self.gen_free:
                f.write("    " + line)
            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_SUFFIX)
            else:
                f.write(AFLPLUSPLUS_SUFFIX)
            f.close()
            return True

        curr_param = func["params"][param_id]
        # print(" -- info: ", func["name"], ", id:", param_id, ", generator_type: ",curr_param["generator_type"])
        if curr_param["generator_type"] == GEN_BUILTIN:
            if curr_param["param_type"].split(" ")[0] in ["volatile", "const"]:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
            else:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_STRING:
            if (curr_param["param_usage"] == "FILE_PATH" or curr_param["param_usage"] == "FILE_PATH_READ" or curr_param["param_usage"] == "FILE_PATH_WRITE" or curr_param["param_usage"] == "FILE_PATH_RW" or curr_param["param_name"] == "filename"):
                curr_gen = self.gen_input_file(curr_param["param_name"])
                self.var_files += 1
                self.dyn_size += 1
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
            else:
                curr_gen = self.gen_string(
                    curr_param["param_type"],
                    curr_param["param_name"],
                    curr_param["parent_type"])
                self.dyn_size += 1
                if (len(curr_param["parent_type"]) > 0):
                    self.buf_size_arr.append("sizeof(char)")
                else:
                    self.buf_size_arr.append("sizeof(char)")
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
                self.curr_gen_string = param_id

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_ENUM:  # GEN_ENUM
            self.gen_this_function = False
            curr_gen = self.gen_enum(
                curr_param["param_type"], curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_ARRAY:  # GEN_ARRAY
            self.gen_this_function = False
            curr_gen = self.gen_array(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_VOID:
            self.gen_this_function = False
            curr_gen = self.gen_void(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_QUALIFIER:
            curr_gen = self.gen_qualifier(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"],
                curr_param["parent_gen"],
                param_id
            )
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            if curr_gen["buf_size"]:
                self.buf_size_arr.append(curr_gen["buf_size"])

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_POINTER:
            curr_gen = self.gen_pointer(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_STRUCT:
            curr_gen = self.gen_struct(
                curr_param["param_name"], curr_param["param_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_INCOMPLETE:
            # iterate all possible variants for generating
            old_func_params = copy.copy(self.gen_func_params)
            old_gen_free = copy.copy(self.gen_free)
            old_dyn_size = copy.copy(self.dyn_size)
            old_buf_size_arr = copy.copy(self.buf_size_arr)
            old_var_function = copy.copy(self.var_function)
            curr_gen = False
            # print(curr_param["param_type"])
            for f in self.target_library['functions']:
                if f["return_type"] == curr_param["param_type"] and f["name"] != func["name"]:
                    # check for function call with simple data type!!!
                    check_params = True
                    for arg in f["params"]:
                        if arg["generator_type"] not in [GEN_BUILTIN, GEN_STRING]:
                            check_params = False
                            break
                    if not check_params:
                        continue

                    curr_gen = self.gen_var_function(func,
                                                     f, curr_param["param_name"])
                    self.var_function += 1
                    self.gen_func_params += curr_gen["gen_lines"]
                    self.gen_free += curr_gen["gen_free"]
                    self.dyn_size += curr_gen["dyn_size"]
                    self.buf_size_arr += curr_gen["buf_size_arr"]
                    param_id += 1
                    self.gen_target_function(func, param_id)

                    param_id -= 1

                    self.gen_func_params = copy.copy(old_func_params)
                    self.gen_free = copy.copy(old_gen_free)
                    self.dyn_size = copy.copy(old_dyn_size)
                    self.buf_size_arr = copy.copy(old_buf_size_arr)
                    self.var_function = copy.copy(old_var_function)

            # curr_gen = self.gen_incomplete(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

        if curr_param["generator_type"] == GEN_FUNCTION:
            self.gen_this_function = False
            # return null pointer to function?
            curr_gen = self.gen_function(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_target_function(func, param_id)

        if curr_param["generator_type"] == GEN_UNKNOWN:  # GEN_UNKNOWN
            self.gen_this_function = False
            return None

    def gen_class_constructor(self, func, param_id) -> bool:
        malloc_free = [
            "unsigned char *",
            "char *",
        ]

        if param_id == len(func['params']):
            if not self.gen_this_function:
                return False
            # If there is no buffer - return!
            if not self.buf_size_arr:
                return False

            f = self.wrapper_file(func)
            if not f:
                return False

            for line in self.gen_header(func["location"].split(':')[0]):
                f.write(line)
            f.write('\n')
            # generate file name
            f = self.wrapper_file(func)
            if not f:
                return False

            for line in self.gen_header(func["location"].split(':')[0]):
                f.write(line)
            f.write('\n')
            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_PREFIX)
            else:
                f.write(AFLPLUSPLUS_PREFIX)
            if self.dyn_size > 0:
                f.write("    if (Fuzz_Size < " + str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write(") return 0;\n")
                f.write(
                    "    size_t dyn_size = (int) ((Fuzz_Size - (" +
                    str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write("))/" + str(self.dyn_size) + ");\n")
            else:
                if len(self.buf_size_arr) > 0:
                    f.write("    if (Fuzz_Size < ")
                    f.write("+".join(self.buf_size_arr))
                    f.write(") return 0;\n")

            f.write("    uint8_t * pos = Fuzz_Data;\n")
            for line in self.gen_func_params:
                f.write("    " + line)

            # Find parent class
            found_parent = None
            for r in self.target_library["records"]:
                if r["hash"] == func["parent_hash"]:
                    found_parent = r
                    break

            if not found_parent:
                self.gen_this_function = False
                return False

            # Find default constructor
            # TODO: add code for other constructors
            f.write("    //declare the RECORD and call constructor\n")
            class_name = found_parent["qname"]
            # print ("Function: ", func["qname"], ", class: ", class_name)
            f.write("    " + class_name + "futag_target" + "(")

            param_list = []
            for arg in func["params"]:
                param_list.append(arg["param_name"] + " ")
            f.write(",".join(param_list))
            f.write(");\n")

            # !attempting free on address which was not malloc()-ed

            f.write("    //FREE\n")
            for line in self.gen_free:
                f.write("    " + line)

            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_SUFFIX)
            else:
                f.write(AFLPLUSPLUS_SUFFIX)
            f.close()
            return True

        curr_param = func["params"][param_id]
        # print(" -- info: ", func["name"], ", id:", param_id, ", generator_type: ",curr_param["generator_type"])
        if curr_param["generator_type"] == GEN_BUILTIN:
            if curr_param["param_type"].split(" ")[0] in ["volatile", "const"]:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
            else:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_STRING:
            if (curr_param["param_usage"] == "FILE_PATH" or curr_param["param_usage"] == "FILE_PATH_READ" or curr_param["param_usage"] == "FILE_PATH_WRITE" or curr_param["param_usage"] == "FILE_PATH_RW" or curr_param["param_name"] == "filename"):
                curr_gen = self.gen_input_file(curr_param["param_name"])
                self.var_files += 1
                self.dyn_size += 1
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
            else:
                curr_gen = self.gen_string(
                    curr_param["param_type"],
                    curr_param["param_name"],
                    curr_param["parent_type"])
                self.dyn_size += 1
                if (len(curr_param["parent_type"]) > 0):
                    self.buf_size_arr.append("sizeof(char)")
                else:
                    self.buf_size_arr.append("sizeof(char)")
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
                self.curr_gen_string = param_id

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_ENUM:  # GEN_ENUM
            self.gen_this_function = False
            curr_gen = self.gen_enum(
                curr_param["param_type"], curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_ARRAY:  # GEN_ARRAY
            self.gen_this_function = False
            curr_gen = self.gen_array(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_VOID:
            self.gen_this_function = False
            curr_gen = self.gen_void(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_QUALIFIER:
            curr_gen = self.gen_qualifier(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"],
                curr_param["parent_gen"],
                param_id
            )
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            if curr_gen["buf_size"]:
                self.buf_size_arr.append(curr_gen["buf_size"])

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_POINTER:
            curr_gen = self.gen_pointer(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_STRUCT:
            curr_gen = self.gen_struct(
                curr_param["param_name"], curr_param["param_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_INCOMPLETE:
            # iterate all possible variants for generating
            old_func_params = copy.copy(self.gen_func_params)
            old_gen_free = copy.copy(self.gen_free)
            old_dyn_size = copy.copy(self.dyn_size)
            old_buf_size_arr = copy.copy(self.buf_size_arr)
            old_var_function = copy.copy(self.var_function)
            curr_gen = False
            # print(curr_param["param_type"])
            for f in self.target_library['functions']:
                if f["return_type"] == curr_param["param_type"] and f["name"] != func["name"]:
                    # check for function call with simple data type!!!
                    check_params = True
                    for arg in f["params"]:
                        if arg["generator_type"] not in [GEN_BUILTIN, GEN_STRING]:
                            check_params = False
                            break
                    if not check_params:
                        continue

                    curr_gen = self.gen_var_function(func,
                                                     f, curr_param["param_name"])
                    self.var_function += 1
                    self.gen_func_params += curr_gen["gen_lines"]
                    self.gen_free += curr_gen["gen_free"]
                    self.dyn_size += curr_gen["dyn_size"]
                    self.buf_size_arr += curr_gen["buf_size_arr"]
                    param_id += 1
                    self.gen_class_constructor(func, param_id)

                    param_id -= 1

                    self.gen_func_params = copy.copy(old_func_params)
                    self.gen_free = copy.copy(old_gen_free)
                    self.dyn_size = copy.copy(old_dyn_size)
                    self.buf_size_arr = copy.copy(old_buf_size_arr)
                    self.var_function = copy.copy(old_var_function)

            # curr_gen = self.gen_incomplete(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

        if curr_param["generator_type"] == GEN_FUNCTION:
            self.gen_this_function = False
            # return null pointer to function?
            curr_gen = self.gen_function(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_constructor(func, param_id)

        if curr_param["generator_type"] == GEN_UNKNOWN:  # GEN_UNKNOWN
            self.gen_this_function = False
            return None

    def gen_class_method(self, func, param_id) -> bool:
        malloc_free = [
            "unsigned char *",
            "char *",
        ]
        # print("param_id: ", param_id)
        # print(func["params"][param_id])
        if param_id == len(func['params']):

            if not self.gen_this_function:
                return False
            # If there is no buffer - return!
            if not self.buf_size_arr:
                return False

            # generate file name
            f = self.wrapper_file(func)
            if not f:
                return False

            for line in self.gen_header(func["location"].split(':')[0]):
                f.write(line)
            f.write('\n')
            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_PREFIX)
            else:
                f.write(AFLPLUSPLUS_PREFIX)

            if self.dyn_size > 0:
                f.write("    if (Fuzz_Size < " + str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write(") return 0;\n")
                f.write(
                    "    size_t dyn_size = (int) ((Fuzz_Size - (" +
                    str(self.dyn_size))
                if self.buf_size_arr:
                    f.write(" + " + "+".join(self.buf_size_arr))
                f.write("))/" + str(self.dyn_size) + ");\n")
            else:
                if len(self.buf_size_arr) > 0:
                    f.write("    if (Fuzz_Size < ")
                    f.write("+".join(self.buf_size_arr))
                    f.write(") return 0;\n")

            f.write("    uint8_t * pos = Fuzz_Data;\n")
            for line in self.gen_func_params:
                f.write("    " + line)

            # Find parent class
            found_parent = None
            for r in self.target_library["records"]:
                if r["hash"] == func["parent_hash"]:
                    found_parent = r
                    break

            if not found_parent:
                self.gen_this_function = False
                return False

            # Find default constructor
            # TODO: add code for other constructors
            found_default_constructor = False
            for fu in self.target_library["functions"]:
                if fu["parent_hash"] == func["parent_hash"] and fu["func_type"] == FUNC_DEFAULT_CONSTRUCTOR:
                    found_default_constructor = True

            # TODO: add code for other constructors!!!
            if not found_default_constructor:
                self.gen_this_function = False
                return False
            f.write("    //declare the RECORD\n")
            class_name = found_parent["qname"]
            # print ("Function: ", func["qname"], ", class: ", class_name)
            # declare the RECORD
            f.write("    " + class_name + " futag_target;")
            # call the method
            f.write("    //METHOD CALL\n")
            f.write("    futag_target." + func["name"]+"(")

            param_list = []
            for arg in func["params"]:
                param_list.append(arg["param_name"] + " ")
            f.write(",".join(param_list))
            f.write(");\n")
            # !attempting free on address which was not malloc()-ed
            f.write("    //FREE\n")
            for line in self.gen_free:
                f.write("    " + line)
            if self.target_type == LIBFUZZER:
                f.write(LIBFUZZER_SUFFIX)
            else:
                f.write(AFLPLUSPLUS_SUFFIX)
            f.close()
            return True

        curr_param = func["params"][param_id]
        # print(" -- info: ", func["name"], ", id:", param_id, ", generator_type: ",curr_param["generator_type"])
        if curr_param["generator_type"] == GEN_BUILTIN:
            if curr_param["param_type"].split(" ")[0] in ["volatile", "const"]:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_SIZE\n",
                                curr_param["param_type"].split(" ")[
                                    1] + " u" + curr_param["param_name"] + " = (" + curr_param["param_type"].split(" ")[1] + ") dyn_size;\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            "gen_free": []
                        }
                    else:
                        curr_gen = {
                            "gen_lines": [
                                "//GEN_BUILTIN\n",
                                curr_param["param_type"].split(
                                    " ")[1] + " u" + curr_param["param_name"] + ";\n",
                                "memcpy(&u" + curr_param["param_name"]+", pos, sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + "));\n",
                                "pos += sizeof(" +
                                curr_param["param_type"].split(" ")[
                                    1] + ");\n",
                                curr_param["param_type"] + " " + curr_param["param_name"] +
                                " = u" + curr_param["param_name"] + ";\n"
                            ],
                            # "gen_free":["free(u" + curr_param["param_name"] + ");\n"]
                            "gen_free": []
                        }
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"].split(" ")[1]+")")
            else:
                if curr_param["param_usage"] == "SIZE_FIELD" and len(func["params"]) > 1:
                    if self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
                else:
                    if self.curr_gen_string == param_id - 1 and self.curr_gen_string >= 0:
                        curr_gen = self.gen_size(
                            curr_param["param_type"], curr_param["param_name"])
                    else:
                        curr_gen = self.gen_builtin(
                            curr_param["param_type"], curr_param["param_name"])
                        self.buf_size_arr.append(
                            "sizeof(" + curr_param["param_type"]+")")
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_STRING:
            if (curr_param["param_usage"] == "FILE_PATH" or curr_param["param_usage"] == "FILE_PATH_READ" or curr_param["param_usage"] == "FILE_PATH_WRITE" or curr_param["param_usage"] == "FILE_PATH_RW" or curr_param["param_name"] == "filename"):
                curr_gen = self.gen_input_file(curr_param["param_name"])
                self.var_files += 1
                self.dyn_size += 1
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
            else:
                curr_gen = self.gen_string(
                    curr_param["param_type"],
                    curr_param["param_name"],
                    curr_param["parent_type"])
                self.dyn_size += 1
                if (len(curr_param["parent_type"]) > 0):
                    self.buf_size_arr.append("sizeof(char)")
                else:
                    self.buf_size_arr.append("sizeof(char)")
                if not curr_gen:
                    self.gen_this_function = False

                self.gen_func_params += curr_gen["gen_lines"]
                self.gen_free += curr_gen["gen_free"]
                self.curr_gen_string = param_id

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_ENUM:  # GEN_ENUM
            self.gen_this_function = False
            curr_gen = self.gen_enum(
                curr_param["param_type"], curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False
            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_ARRAY:  # GEN_ARRAY
            self.gen_this_function = False
            curr_gen = self.gen_array(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_VOID:
            self.gen_this_function = False
            curr_gen = self.gen_void(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_QUALIFIER:
            curr_gen = self.gen_qualifier(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"],
                curr_param["parent_gen"],
                param_id
            )
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            if curr_gen["buf_size"]:
                self.buf_size_arr.append(curr_gen["buf_size"])

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_POINTER:
            curr_gen = self.gen_pointer(
                curr_param["param_type"],
                curr_param["param_name"],
                curr_param["parent_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_STRUCT:
            curr_gen = self.gen_struct(
                curr_param["param_name"], curr_param["param_type"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_INCOMPLETE:
            # iterate all possible variants for generating
            old_func_params = copy.copy(self.gen_func_params)
            old_gen_free = copy.copy(self.gen_free)
            old_dyn_size = copy.copy(self.dyn_size)
            old_buf_size_arr = copy.copy(self.buf_size_arr)
            old_var_function = copy.copy(self.var_function)
            curr_gen = False
            # print(curr_param["param_type"])
            for f in self.target_library['functions']:
                if f["return_type"] == curr_param["param_type"] and f["name"] != func["name"]:
                    # check for function call with simple data type!!!
                    check_params = True
                    for arg in f["params"]:
                        if arg["generator_type"] not in [GEN_BUILTIN, GEN_STRING]:
                            check_params = False
                            break
                    if not check_params:
                        continue

                    curr_gen = self.gen_var_function(func,
                                                     f, curr_param["param_name"])
                    self.var_function += 1
                    self.gen_func_params += curr_gen["gen_lines"]
                    self.gen_free += curr_gen["gen_free"]
                    self.dyn_size += curr_gen["dyn_size"]
                    self.buf_size_arr += curr_gen["buf_size_arr"]
                    param_id += 1
                    self.gen_class_method(func, param_id)

                    param_id -= 1

                    self.gen_func_params = copy.copy(old_func_params)
                    self.gen_free = copy.copy(old_gen_free)
                    self.dyn_size = copy.copy(old_dyn_size)
                    self.buf_size_arr = copy.copy(old_buf_size_arr)
                    self.var_function = copy.copy(old_var_function)

            # curr_gen = self.gen_incomplete(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

        if curr_param["generator_type"] == GEN_FUNCTION:
            self.gen_this_function = False
            # return null pointer to function?
            curr_gen = self.gen_function(curr_param["param_name"])
            if not curr_gen:
                self.gen_this_function = False

            self.gen_func_params += curr_gen["gen_lines"]
            self.gen_free += curr_gen["gen_free"]
            self.buf_size_arr.append("sizeof(" + curr_param["param_type"]+")")

            param_id += 1
            self.gen_class_method(func, param_id)

        if curr_param["generator_type"] == GEN_UNKNOWN:  # GEN_UNKNOWN
            self.gen_this_function = False
            return None

    def gen_targets(self):
        C_generated_function = []
        Cplusplus_usual_class_method = []
        Cplusplus_static_class_method = []
        for func in self.target_library["functions"]:
            # For C
            if func["access_type"] == AS_NONE and func["fuzz_it"] and func["storage_class"] < 2 and (not "(anonymous namespace)" in func["qname"]) and (func["parent_hash"] == ""):
                print("-- [Futag] Trying generate fuzz-driver for function: ", func["name"], "!")
                self.gen_func_params = []
                self.gen_free = []
                self.gen_this_function = True
                self.buf_size_arr = []
                self.dyn_size = 0
                self.curr_gen_string = -1
                self.gen_target_function(func, 0)
                if self.gen_this_function:
                    print("-- [Futag] Fuzz-driver for function: ",
                          func["name"], " generated!")
                    C_generated_function.append(func["name"])
                # C_generated_function.append(func["name"])
            # For C++, Declare object of class and then call the method
            if func["access_type"] == AS_PUBLIC and func["fuzz_it"] and func["func_type"] in [FUNC_CXXMETHOD, FUNC_CONSTRUCTOR, FUNC_DEFAULT_CONSTRUCTOR, FUNC_GLOBAL, FUNC_STATIC]:
                if (not "(anonymous namespace)" in func["qname"]) and (not "::operator" in func["qname"]):
                    Cplusplus_usual_class_method.append(func["qname"])
                    self.gen_func_params = []
                    self.gen_free = []
                    self.gen_this_function = True
                    self.buf_size_arr = []
                    self.dyn_size = 0
                    self.curr_gen_string = -1
                    if func["func_type"] in [FUNC_CONSTRUCTOR, FUNC_DEFAULT_CONSTRUCTOR]:
                        self.gen_class_constructor(func, 0)
                        if self.gen_this_function:
                            print("-- [Futag] Fuzz-driver for for constructor: ", func["name"], " generated!")
                    else:
                        self.gen_class_method(func, 0)
                        if self.gen_this_function:
                            print("-- [Futag] Fuzz-driver for for method: ",
                                  func["name"], " generated!")

            # For C++, Call the static function of class without declaring object
            if func["access_type"] in [AS_NONE, AS_PUBLIC] and func["fuzz_it"] and func["func_type"] in [FUNC_CXXMETHOD, FUNC_GLOBAL, FUNC_STATIC] and func["storage_class"] == SC_STATIC:
                # print("-- [Futag] Trying generate fuzz-driver for static method: ",func["name"], "!")
                if (not "(anonymous namespace)" in func["qname"]) and (not "::operator" in func["qname"]):
                    Cplusplus_static_class_method.append(func["qname"])
                    # self.gen_func_params = []
                    # self.gen_free = []
                    # self.gen_this_function = True
                    # self.buf_size_arr = []
                    # self.dyn_size = 0
                    # self.curr_gen_string = -1
                    # self.gen_class_constructor(func, 0)
                    # if self.gen_this_function:
                    #     self.gen_class_method(func, 0)
                    #     print("-- [Futag] Fuzz-driver for for method: ",func["name"], " generated!")

            # We dont generate for static function of C
            if func["func_type"] == FUNC_UNKNOW_RECORD and func["storage_class"] == 2:
                continue
        self.result_report = {
            "C_generated_function": C_generated_function,
            "Cplusplus_static_class_method": Cplusplus_static_class_method,
            "Cplusplus_usual_class_method": Cplusplus_usual_class_method
        }
        json.dump(self.result_report, open(
            (self.tmp_output_path / "result-report.json").as_posix(), "w"))

    def compile_driver_worker(self, bgen_args):
        p = Popen(
            bgen_args,
            stdout=PIPE,
            stderr=PIPE,
            universal_newlines=True,
        )
        output, errors = p.communicate()
        if p.returncode:
            print(" ".join(p.args))
            print("\n-- [Futag] ERROR:", errors)
        else:
            print("-- [Futag] Fuzz-driver has been compiled successfully!")

    def compile_targets(self, workers: int = 4, flags: str = FUZZ_COMPILER_FLAGS):
        """
        Parameters
        ----------
        workers: int
            number of processes for compiling, default to 4.
        flags: str
            flags for compiling fuzz-drivers, default to "-fsanitize=address,fuzzer -g -O0".
        """
        include_subdir: List[pathlib.Path] = [
            x for x in (self.library_root).iterdir() if x.is_dir()]
        include_subdir = include_subdir + \
            [x for x in (self.build_path).iterdir() if x.is_dir()]
        include_subdir = include_subdir + \
            [x for x in (self.install_path).iterdir() if x.is_dir()]
        if (self.install_path / "include").exists():
            include_subdir = include_subdir + \
                [x for x in (self.install_path /
                             "include").iterdir() if x.is_dir()]
        generated_functions = [
            x for x in self.tmp_output_path.iterdir() if x.is_dir()]
        generated_targets = 0
        compiler_flags_libFuzzer = flags
        compiler_flags_aflplusplus = flags
        compiler_path = ""
        if self.target_type == LIBFUZZER:
            compiler_path = self.futag_llvm_package / "bin/clang++"
        else:
            compiler_path = self.futag_llvm_package / \
                "AFLplusplus/usr/local/bin/afl-clang-fast"
        compile_cmd_list = []
        static_lib = []
        target_lib = [u for u in (self.library_root).glob(
            "**/*.a") if u.is_file()]
        if target_lib:
            static_lib = ["-Wl,--start-group"]
            for t in target_lib:
                static_lib.append(t.as_posix())
            static_lib.append("-Wl,--end-group")
        driver_output = []
        for func_dir in generated_functions:
            # Extract compiler cwd, to resolve relative includes
            current_func = [
                f for f in self.target_library['functions'] if f['qname'] == func_dir.name][0]
            current_file = current_func["location"].split(':')[0]
            current_func_compilation_opts = ""
            compilation_opts = ""

            for compiled_file in self.target_library["compiled_files"]:
                if current_file == compiled_file["filename"]:
                    compilation_opts = compiled_file["compiler_opts"]
            current_func_compilation_opts = compilation_opts.split(' ')
            # Extract all include locations from compilation options
            include_paths: List[pathlib.Path] = map(
                pathlib.Path,
                map(
                    current_func_compilation_opts.__getitem__,
                    [i + 1 for i,
                        x in enumerate(current_func_compilation_opts) if x == '-I']
                ))

            resolved_include_paths: List[pathlib.Path] = []
            for include_path in include_paths:
                if include_path.is_absolute():
                    resolved_include_paths.append(include_path)
                else:
                    # Resolve relative include paths (e.g. in this case: -I.. -I.)
                    resolved_include_paths.append(
                        pathlib.Path(include_path).absolute())

            current_include = []

            for i in include_subdir:
                current_include.append("-I" + i.as_posix())
            for i in resolved_include_paths:
                current_include.append("-I" + i.as_posix())
            fuzz_driver_dirs = [x for x in func_dir.iterdir() if x.is_dir()]
            for dir in fuzz_driver_dirs:
                for target_src in [t for t in dir.glob("*"+self.target_extension) if t.is_file()]:
                    generated_targets += 1

                    driver_output.append([current_include, target_src.as_posix(
                    ), dir.as_posix() + "/" + target_src.stem + ".out"])

                    if self.target_type == LIBFUZZER:
                        compiler_cmd = [compiler_path.as_posix()] + compiler_flags_libFuzzer.split(" ") + current_include + [
                            target_src.as_posix()] + ["-o"] + [dir.as_posix() + "/" + target_src.stem + ".out"] + static_lib
                    else:
                        compiler_cmd = [compiler_path.as_posix()] + compiler_flags_aflplusplus.split(" ") + current_include + [
                            target_src.as_posix()] + ["-o"] + [dir.as_posix() + "/" + target_src.stem + ".out"] + static_lib

                    target_file = open(target_src.as_posix(), "a")
                    target_file.write("\n//Compile command:")
                    target_file.write("\n/*\n")
                    target_file.write(" ".join(compiler_cmd))
                    target_file.write("\n*/\n")
                    target_file.close()
                    compile_cmd_list.append(compiler_cmd)
        with Pool(workers) as p:
            p.map(self.compile_driver_worker, compile_cmd_list)

        # Extract the results of compilation
        compiled_targets_list = [
            x for x in self.tmp_output_path.glob("**/*.out") if x.is_file()]
        for compiled_target in compiled_targets_list:
            copy_tree(compiled_target.parents[1].as_posix(), self.output_path.as_posix())
        print(
            "-- [Futag] Result of compiling: "
            + str(len(compiled_targets_list))
            + " fuzz-driver(s)\n"
        )
