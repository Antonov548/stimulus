"""GNU R interface, see http://www.r-project.org

TODO: free memory when CTRL+C pressed, even on Windows
"""

import re

from textwrap import indent
from typing import Iterable, IO, Optional, Tuple

from stimulus.model import ParamMode, ParamSpec
from stimulus.model.functions import FunctionDescriptor

from .base import (
    BlockBasedCodeGenerator,
    SingleBlockCodeGenerator,
)


class RRCodeGenerator(SingleBlockCodeGenerator):
    def generate_function(self, function: str, out: IO[str]) -> None:
        # Check types
        self.check_types_of_function(function)

        # Get function specification
        spec = self.get_function_descriptor(function)

        # Derive name of R function
        name = spec.get_name_in_generated_code("R")

        ## Roxygen to export the function
        if not spec.is_internal:
            out.write("#' @export\n")

        ## Header
        ## do_par handles the translation of a single argument in the
        ## header. Pretty simple, the only difficulty is that we
        ## might need to add default values. Default values are taken
        ## from a language specific dictionary, this is compiled from
        ## the type file(s).

        ## So we take all arguments with mode 'IN' or 'INOUT' and
        ## check whether they have a default value. If yes then we
        ## check if the default value is given in the type file. If
        ## yes then we use the value given there, otherwise the
        ## default value is ignored silently. (Not very nice.)

        out.write(name)
        out.write(" <- function(")

        def handle_input_argument(param: ParamSpec) -> str:
            tname = param.type
            type_desc = self.get_type_descriptor(tname)
            header = param.name.replace("_", ".")
            if "HEADER" in type_desc:
                header = type_desc["HEADER"]
            if header:
                header = header.replace("%I%", param.name.replace("_", "."))
            else:
                header = ""

            if param.default is not None:
                default = type_desc.translate_default_value(param.default)
            else:
                default = ""

            if default:
                header = f"{header}={default}"

            for i, dep in enumerate(param.dependencies):
                header = header.replace("%I" + str(i + 1) + "%", dep)

            if re.search("%I[0-9]*%", header):
                self.log.error(
                    f"Missing HEADER dependency for {tname} {param.name} in function {name}"
                )

            return header

        head = [
            handle_input_argument(param)
            for param in spec.iter_parameters()
            if param.is_input
        ]
        head = [h for h in head if h != ""]
        out.write(", ".join(head))
        out.write(") {\n")

        ## Argument checks, INCONV
        ## We take 'IN' and 'INOUT' mode arguments and if they have an
        ## INCONV field then we use that. This is typically for
        ## argument checks, like we check here that the argument
        ## supplied for a graph is indeed an igraph graph object. We
        ## also covert numeric vectors to 'double' here.

        ## The INCONV fields are simply concatenated by newline
        ## characters.
        out.write("  # Argument checks\n")

        def handle_argument_check(param: ParamSpec) -> str:
            tname = param.type
            t = self.get_type_descriptor(tname)
            mode = param.mode_str
            if param.is_input and "INCONV" in t:
                if mode in t["INCONV"]:
                    res = "  " + t["INCONV"][mode]
                else:
                    res = "  " + t["INCONV"]
            else:
                res = ""
            res = res.replace("%I%", param.name.replace("_", "."))

            for i, dep in enumerate(param.dependencies):
                res = res.replace("%I" + str(i + 1) + "%", dep)

            if re.search("%I[0-9]*%", res):
                self.log.error(
                    f"Missing IN dependency for {tname} {param.name} in function {name}"
                )

            return res

        inconv = [handle_argument_check(param) for param in spec.iter_parameters()]
        inconv = [i for i in inconv if i != ""]
        out.write("\n".join(inconv) + "\n\n")

        ## Function call
        ## This is a bit more difficult than INCONV. Here we supply
        ## each argument to the .Call function, if the argument has a
        ## 'CALL' field then it is used, otherwise we simply use its
        ## name.
        ##
        ## Note that arguments with empty CALL fields are
        ## completely ignored, so giving an empty CALL field is
        ## different than not giving it at all.

        out.write("  on.exit( .Call(C_R_igraph_finalizer) )\n")
        out.write("  # Function call\n")
        out.write("  res <- .Call(C_R_" + function + ", ")

        parts = []
        for param in spec.iter_parameters():
            if param.is_input:
                type = self.get_type_descriptor(param.type)
                name = param.name.replace("_", ".")
                call = type.get("CALL", name)
                if call:
                    parts.append(call.replace("%I%", name))

        out.write(", ".join(parts))
        out.write(")\n")

        ## Output conversions
        def handle_output_argument(
            param: ParamSpec,
            realname: Optional[str] = None,
            *,
            iprefix: str = "",
        ):
            if realname is None:
                realname = param.name

            tname = param.type
            t = self.get_type_descriptor(tname)
            mode = param.mode_str
            if "OUTCONV" in t and mode in t["OUTCONV"]:
                outconv = "  " + t["OUTCONV"][mode]
            else:
                outconv = ""
            outconv = outconv.replace("%I%", iprefix + realname)

            for i, dep in enumerate(param.dependencies):
                outconv = outconv.replace("%I" + str(i + 1) + "%", dep)

            if re.search("%I[0-9]*%", outconv):
                self.log.error(
                    f"Missing OUT dependency for {tname} {param.name} in function {name}"
                )

            return re.sub("%I[0-9]+%", "", outconv)

        retpars = [param.name for param in spec.iter_parameters() if param.is_output]

        if len(retpars) <= 1:
            outconv = [
                handle_output_argument(param, "res") for param in spec.iter_parameters()
            ]
        else:
            outconv = [
                handle_output_argument(param, iprefix="res$")
                for param in spec.iter_parameters()
            ]

        outconv = [o for o in outconv if o != ""]

        if len(retpars) == 0:
            # returning the return value of the function
            rt = self.get_type_descriptor(spec.return_type)
            if "OUTCONV" in rt:
                retconv = "  " + rt["OUTCONV"]["OUT"]
            else:
                retconv = ""
            retconv = retconv.replace("%I%", "res")
            # TODO: %I1% etc, is not handled here!
            ret = "\n".join(outconv) + "\n" + retconv + "\n"
        elif len(retpars) == 1:
            # returning a single output value
            ret = "\n".join(outconv) + "\n"
        else:
            # returning a list of output values
            ret = "\n".join(outconv) + "\n"
        out.write(ret)

        ## Some graph attributes to add
        if "R" not in spec:
            # Convert legacy "GATTR-R", "GATTR-PARAM-R", "CLASS-R" and "PP-R"
            r_namespace = {}
            for key in ("GATTR", "GATTR-PARAM", "CLASS", "PP"):
                r_key = f"{key}-R"
                if r_key in spec:
                    r_namespace[key] = spec[r_key]
            if r_namespace:
                spec._obj["R"] = r_namespace

        r_spec = spec._obj.get("R", {})

        ## Add graph attributes
        if "GATTR" in r_spec:
            gattrs = r_spec["GATTR"]
            gattrs_dict = {}
            if isinstance(gattrs, dict):
                gattrs_dict.update(gattrs)
            else:
                for item in gattrs.split(","):
                    name, value = item.split(" IS ", 1)
                    name = name.strip()
                    value = value.strip()
                    gattrs_dict[name] = value
            if gattrs_dict:
                out.write(
                    "\n".join(
                        f"  res <- set.graph.attribute(res, '{name}', '{val}')"
                        for name, val in gattrs_dict.items()
                    )
                    + "\n"
                )

        ## Add some parameters as additional graph attributes
        if "GATTR-PARAM" in r_spec:
            pars = r_spec["GATTR-PARAM"].split(",")
            pars = [p.strip().replace("_", ".") for p in pars]
            sstr = "  res <- set.graph.attribute(res, '{par}', {par})\n"
            for p in pars:
                out.write(sstr.format(par=p))

        ## Set the class if requested
        if "CLASS" in r_spec:
            out.write(f'  class(res) <- "{r_spec["CLASS"]}"\n')

        ## See if there is a postprocessor
        if "PP" in r_spec:
            out.write(f'  res <- {r_spec["PP"]}(res)\n')

        out.write("  res\n}\n\n")


class RCCodeGenerator(SingleBlockCodeGenerator):
    def generate_function(self, function: str, out: IO[str]) -> None:
        # Check types
        self.check_types_of_function(function, errors="error")

        desc = self.get_function_descriptor(function)

        ## Compile the output
        ## This code generator is quite difficult, so we use different
        ## functions to generate the approprite chunks and then
        ## compile them together using a simple template.
        ## See the documentation of each chunk below.
        res = {}
        res["func"] = function
        res["header"] = self.chunk_header(desc)
        res["decl"] = self.chunk_declaration(desc)
        res["inconv"] = self.chunk_inconv(desc)
        res["call"] = self.chunk_call(desc)
        res["outconv"] = self.chunk_outconv(desc)

        # Replace into the template
        text = (
            """
/*-------------------------------------------/
/ %(func)-42s /
/-------------------------------------------*/
%(header)s {
                                        /* Declarations */
%(decl)s
                                        /* Convert input */
%(inconv)s
                                        /* Call igraph */
%(call)s
                                        /* Convert output */
%(outconv)s

  UNPROTECT(1);
  return(result);
}\n"""
            % res
        )

        out.write(text)

    def chunk_header(self, desc: FunctionDescriptor) -> str:
        """The header. All functions return with a 'SEXP', so this is
        easy. We just take the 'IN' and 'INOUT' arguments, all will
        have type SEXP, and concatenate them by commas. The function name
        is created by prefixing the original name with 'R_'.
        """

        def do_par(spec: ParamSpec) -> str:
            t = self.get_type_descriptor(spec.type)
            if "HEADER" in t:
                if t["HEADER"]:
                    return t["HEADER"].replace("%I%", spec.name)
                else:
                    return ""
            else:
                return spec.name

        inout = [do_par(spec) for spec in desc.iter_parameters() if spec.is_input]
        inout = ["SEXP " + n for n in inout if n != ""]
        return "SEXP R_" + desc.name + "(" + ", ".join(inout) + ")"

    def chunk_declaration(self, desc: FunctionDescriptor) -> str:
        """There are a couple of things to declare. First a C type is
        needed for every argument, these will be supplied in the C
        igraph call. Then, all 'OUT' arguments need a SEXP variable as
        well, the result will be stored here. The return type
        of the C function also needs to be declared, that comes
        next. The result and names SEXP variables will contain the
        final result, these are last. ('names' is not always used, but
        it is easier to always declare it.)
        """

        def do_par(spec: ParamSpec) -> str:
            type_desc = self.get_type_descriptor(spec.type)
            return type_desc.declare_c_variable(f"c_{spec.name}", mode=spec.mode)

        inout = [do_par(spec) for spec in desc.iter_parameters()]
        out = [
            f"SEXP {spec.name};"
            for spec in desc.iter_parameters()
            if spec.mode is ParamMode.OUT
        ]

        retpars = [spec.name for spec in desc.iter_parameters() if spec.is_output]

        return_type_desc = self.get_type_descriptor(desc.return_type)
        retdecl = return_type_desc.declare_c_variable("c_result") if not retpars else ""

        if len(retpars) <= 1:
            res = "\n".join(inout + out + [retdecl] + ["SEXP result;"])
        else:
            res = "\n".join(inout + out + [retdecl] + ["SEXP result, names;"])
        return indent(res, "  ")

    def chunk_inconv(self, desc: FunctionDescriptor) -> str:
        """Input conversions. Not only for types with mode 'IN' and
        'INOUT', eg. for 'OUT' vector types we need to allocate the
        required memory here, do all the initializations, etc. Types
        without INCONV fields are ignored. The usual %C%, %I% is
        performed at the end.
        """

        def do_par(param: ParamSpec) -> str:
            cname = "c_" + param.name
            t = self.get_type_descriptor(param.type)
            mode = param.mode_str
            if "INCONV" in t and mode in t["INCONV"]:
                inconv = "  " + t["INCONV"][mode]
            else:
                inconv = ""

            for i, dep in enumerate(param.dependencies):
                inconv = inconv.replace("%C" + str(i + 1) + "%", "c_" + dep)

            return inconv.replace("%C%", cname).replace("%I%", param.name)

        inconv = [do_par(param) for param in desc.iter_parameters()]
        inconv = [i for i in inconv if i != ""]

        return "\n".join(inconv)

    def chunk_call(self, desc: FunctionDescriptor) -> str:
        """Every single argument is included, independently of their
        mode. If a type has a 'CALL' field then that is used after the
        usual %C% and %I% substitutions, otherwise the standard 'c_'
        prefixed C argument name is used.
        """

        calls = []
        for param in desc.iter_parameters():
            type = self.get_type_descriptor(param.type).get("CALL", f"c_{param.name}")

            if isinstance(type, dict):
                call = type.get(param.mode_str, "")
            else:
                call = type

            if call:
                call = call.replace("%C%", f"c_{param.name}").replace("%I%", param.name)
                calls.append(call)

        retpars = [param.name for param in desc.iter_parameters() if param.is_output]
        calls = ", ".join(calls)
        res = f"  {desc.name}({calls});\n"
        if not retpars:
            res = f"  c_result={res}"
        return res

    def chunk_outconv(self, spec: FunctionDescriptor) -> str:
        """The output conversions, this is quite difficult. A function
        may report its results in two ways: by returning it directly
        or by setting a variable to which a pointer was passed. igraph
        usually uses the latter and returns error codes, except for
        some simple functions like 'igraph_vcount()' which cannot
        fail.

        First we add the output conversion for all types. This is
        easy. Note that even 'IN' arguments may have output
        conversion, eg. this is the place to free memory allocated to
        them in the 'INCONV' part.

        Then we check how many 'OUT' or 'INOUT' arguments we
        have. There are three cases. If there is a single such
        argument then that is already converted and we need to return
        that. If there is no such argument then the output of the
        function was returned, so we perform the output conversion for
        the returned type and this will be the result. If there are
        more than one 'OUT' and 'INOUT' arguments then they are
        collected in a named list. The names come from the argument
        names.
        """

        def do_par(param: ParamSpec) -> str:
            cname = f"c_{param.name}"
            t = self.get_type_descriptor(param.type)
            mode = param.mode_str
            if "OUTCONV" in t and mode in t["OUTCONV"]:
                outconv = "  " + t["OUTCONV"][mode]
            else:
                outconv = ""

            for i, dep in enumerate(param.dependencies):
                outconv = outconv.replace("%C" + str(i + 1) + "%", "c_" + dep)

            return outconv.replace("%C%", cname).replace("%I%", param.name)

        outconv = [do_par(param) for param in spec.iter_parameters()]
        outconv = [o for o in outconv if o != ""]

        retpars = [param.name for param in spec.iter_parameters() if param.is_output]
        if not retpars:
            # return the return value of the function
            rt = self.get_type_descriptor(spec.return_type)
            if "OUTCONV" in rt:
                retconv = "  " + rt["OUTCONV"]["OUT"]
            else:
                retconv = ""
            retconv = retconv.replace("%C%", "c_result").replace("%I%", "result")
            ret = "\n".join(outconv) + "\n" + retconv
        elif len(retpars) == 1:
            # return the single output value
            retconv = "  result=" + retpars[0] + ";"
            ret = "\n".join(outconv) + "\n" + retconv
        else:
            # create a list of output values
            sets = [
                f"  SET_VECTOR_ELT(result, {index}, {name});"
                for index, name in enumerate(retpars)
            ]
            names = [
                f'  SET_STRING_ELT(names, {index}, CREATE_STRING_VECTOR("{name}"));'
                for index, name in enumerate(retpars)
            ]
            ret = "\n".join(
                [
                    f"  PROTECT(result=NEW_LIST({len(retpars)}));",
                    f"  PROTECT(names=NEW_CHARACTER({len(retpars)}));",
                ]
                + outconv
                + sets
                + names
                + ["  SET_NAMES(result, names);", f"  UNPROTECT({len(sets) + 1});"]
            )

        return ret


class RInitCodeGenerator(BlockBasedCodeGenerator):
    def _count_arguments(self, name: str) -> Tuple[int, int]:
        desc = self.get_function_descriptor(name)
        in_args, out_args = 0, 0
        for param in desc.iter_parameters():
            if param.type in ("DEPRECATED", "NULL"):
                continue
            if param.is_input:
                in_args += 1
            if param.is_output:
                out_args += 1
        return in_args, out_args

    def generate_declarations_block(self, out: IO[str]) -> None:
        for name in self.iter_functions():
            self.generate_declaration(name, out)

    def generate_declaration(self, name: str, out: IO[str]) -> None:
        num_input_args, num_output_args = self._count_arguments(name)

        # One output argument is used for the return value. The default generated
        # R wrapper only uses the first output argument.
        num_r_args = num_input_args

        args = ", ".join(["SEXP"] * num_r_args)
        out.write(f"extern SEXP R_{name}({args});\n")

    def generate_function(self, name: str, out: IO[str]) -> None:
        num_input_args, num_output_args = self._count_arguments(name)
        num_r_args = "{:>2}".format(num_input_args)
        padding = " " * (50 - len(name))
        out.write(
            f'    {{"R_{name}",{padding}(DL_FUNC) &R_{name},{padding}{num_r_args}}},\n'
        )

    def iter_functions(self, include_ignored: bool = False) -> Iterable[str]:
        return sorted(super().iter_functions(include_ignored=include_ignored))
