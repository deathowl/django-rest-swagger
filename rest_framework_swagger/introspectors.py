# coding=utf-8
"""Handles the instrospection of REST Framework Views and ViewSets."""
from abc import ABCMeta, abstractmethod
import re

from django.contrib.admindocs.utils import trim_docstring
from django.utils.html import escape
import markdown
from rest_framework.views import get_view_name, get_view_description


def get_resolved_value(obj, attr, default=None):
    value = getattr(obj, attr, default)
    if callable(value):
        value = value()
    return value


class IntrospectorHelper(object):
    __metaclass__ = ABCMeta

    @staticmethod
    def strip_params_from_docstring(docstring):
        """
        Strips the params from the docstring (ie. myparam -- Some param)
        """
        split_lines = trim_docstring(docstring).split('\n')

        cut_off = None
        for index, line in enumerate(split_lines):
            line = line.strip()
            if line.find('--') != -1:
                cut_off = index
                break
        if cut_off is not None:
            split_lines = split_lines[0:cut_off]

        return u'\n'.join(split_lines)

    @staticmethod
    def get_serializer_name(serializer):
        if serializer is None:
            return None

        return serializer.__name__

    @staticmethod
    def get_view_description(callback):
        """
        Returns the first sentence of the first line of the class docstring
        """
        return get_view_description(callback).split("\n")[0].split(".")[0]


class BaseViewIntrospector(object):
    __metaclass__ = ABCMeta

    def __init__(self, callback, path, pattern):
        self.callback = callback
        self.path = path
        self.pattern = pattern

    @abstractmethod
    def __iter__(self):
        pass

    def get_iterator(self):
        return self.__iter__()

    def get_serializer_class(self):
        if hasattr(self.callback, 'get_serializer_class'):
            return self.callback().get_serializer_class()

    def get_description(self):
        """
        Returns the first sentence of the first line of the class docstring
        """
        return IntrospectorHelper.get_view_description(self.callback)


class BaseMethodIntrospector(object):
    __metaclass__ = ABCMeta

    def __init__(self, view_introspector, method):
        self.method = method
        self.parent = view_introspector
        self.callback = view_introspector.callback
        self.path = view_introspector.path

    def get_serializer_class(self):
        return self.parent.get_serializer_class()

    def get_summary(self):
        docs = self.get_docs()

        # If there is no docstring on the method, get class docs
        if docs is None:
            docs = self.parent.get_description()
        docs = trim_docstring(docs).split('\n')[0]

        return docs

    def get_nickname(self):
        """ Returns the APIView's nickname """
        return get_view_name(self.callback).replace(' ', '_')

    def get_notes(self):
        """
        Returns the body of the docstring trimmed before any parameters are
        listed. First, get the class docstring and then get the method's. The
        methods will always inherit the class comments.
        """
        docstring = ""

        class_docs = trim_docstring(get_view_description(self.callback))
        method_docs = trim_docstring(self.get_docs())

        if class_docs is not None:
            docstring += class_docs
        if method_docs is not None:
            docstring += '\n\n' + method_docs

        docstring = IntrospectorHelper.strip_params_from_docstring(docstring)
        docstring = markdown.markdown(escape(docstring))

        return docstring

    def get_parameters(self):
        """
        Returns parameters for an API. Parameters are a combination of HTTP
        query parameters as well as HTTP body parameters that are defined by
        the DRF serializer fields
        """
        params = []
        path_params = self.build_path_parameters()
        body_params = self.build_body_parameters()
        form_params = self.build_form_parameters()
        parsed_params = self.parse_params_from_docstring()

        if path_params:
            params += path_params

        if self.get_http_method() not in ["GET", "DELETE"]:
            params += form_params

            if not form_params and body_params is not None:
                params.append(body_params)

        if parsed_params:
            params += parsed_params

        return params

    def get_http_method(self):
        return self.method

    @abstractmethod
    def get_docs(self):
        return ''

    def retrieve_docstring(self):
        """
        Attempts to fetch the docs for a class method. Returns None
        if the method does not exist
        """
        method = str(self.method).lower()
        if not hasattr(self.callback, method):
            return None
        return getattr(self.callback, method).__doc__

    def build_body_parameters(self):
        serializer = self.get_serializer_class()
        serializer_name = IntrospectorHelper.get_serializer_name(serializer)

        if serializer_name is None:
            return

        return {
            'name': serializer_name,
            'dataType': serializer_name,
            'paramType': 'body',
        }

    def build_path_parameters(self):
        """
        Gets the parameters from the URL
        """
        url_params = re.findall('/{([^}]*)}', self.path)
        params = []

        for param in url_params:
            params.append({
                'name': param,
                'dataType': 'string',
                'paramType': 'path',
                'required': True
            })

        return params

    def build_form_parameters(self):
        """
        Builds form parameters from the serializer class
        """
        data = []
        serializer = self.get_serializer_class()

        if serializer is None:
            return data

        fields = serializer().get_fields()

        for name, field in fields.items():

            if getattr(field, 'read_only', False):
                continue

            data_type = field.type_label
            max_length = getattr(field, 'max_length', None)
            min_length = getattr(field, 'min_length', None)
            allowable_values = None

            if max_length is not None or min_length is not None:
                allowable_values = {
                    'max': max_length,
                    'min': min_length,
                    'valueType': 'RANGE'
                }

            data.append({
                'paramType': 'form',
                'name': name,
                'dataType': data_type,
                'allowableValues': allowable_values,
                'description': getattr(field, 'help_text', ''),
                'defaultValue': get_resolved_value(field, 'default'),
                'required': getattr(field, 'required', None)
            })

        return data

    def parse_params_from_docstring(self):
        params = []

        docstring = self.retrieve_docstring()
        if docstring is None:
            docstring = ''
        docstring += "\n" + get_view_description(self.callback)

        if docstring is None:
            return params

        split_lines = docstring.split('\n')

        for line in split_lines:
            param = line.split(' -- ')
            if len(param) == 2:

                param_name = param[0].strip()
                param_description = param[1].strip()
                param_type = 'query'
                param_types = {
                    'GET': 'query',
                    'POST': 'form',
                    'HEADER': 'header',
                }
                data_type = 'string'

                match = re.search(r'^(GET|POST|HEADER):(.*)', param_name)
                if match:
                    param_type = param_types.get(match.group(1))
                    param_name = match.group(2).strip()

                match = re.search(r'(.*)\((.*)\)', param_description)
                if match:
                    param_description = match.group(1).strip()
                    data_type = match.group(2)

                params.append({'paramType': escape(param_type),
                               'name': escape(param_name),
                               'description': escape(param_description),
                               'dataType': escape(data_type)})

        return params


class APIViewIntrospector(BaseViewIntrospector):
    def __iter__(self):
        methods = self.callback().allowed_methods
        for method in methods:
            yield APIViewMethodIntrospector(self, method)


class APIViewMethodIntrospector(BaseMethodIntrospector):
    def get_docs(self):
        """
        Attempts to retrieve method specific docs for an
        endpoint. If none are available, the class docstring
        will be used
        """
        return self.retrieve_docstring()


class ViewSetIntrospector(BaseViewIntrospector):
    """Handle ViewSet introspection."""

    def __iter__(self):
        methods = self._resolve_methods()
        for method in methods:
            yield ViewSetMethodIntrospector(self, methods[method], method)

    def _resolve_methods(self):
        if not hasattr(self.pattern.callback, 'func_code') or \
                not hasattr(self.pattern.callback, 'func_closure') or \
                not hasattr(self.pattern.callback.func_code, 'co_freevars') or \
                'actions' not in self.pattern.callback.func_code.co_freevars:
            raise RuntimeError('Unable to use callback invalid closure/function specified.')

        idx = self.pattern.callback.func_code.co_freevars.index('actions')
        return self.pattern.callback.func_closure[idx].cell_contents


class ViewSetMethodIntrospector(BaseMethodIntrospector):
    def __init__(self, view_introspector, method, http_method):
        super(ViewSetMethodIntrospector, self).__init__(view_introspector, method)
        self.http_method = http_method.upper()

    def get_http_method(self):
        return self.http_method

    def get_docs(self):
        """
        Attempts to retrieve method specific docs for an
        endpoint. If none are available, the class docstring
        will be used
        """
        return self.retrieve_docstring()
