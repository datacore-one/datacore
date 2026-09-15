"""Unambiguous string-key mappings for identity and execution configuration."""
import yaml


class UniqueStringKeyLoader(yaml.SafeLoader):
    error_type = ValueError
    context = 'configuration'

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise self.error_type(self.context + ' contains a duplicate or non-string key')
            result[key] = self.construct_object(value_node, deep=deep)
        return result
