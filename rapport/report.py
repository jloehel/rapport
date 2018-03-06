# Copyright 2013 Sascha Peilicke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import codecs
import glob
import os
import shutil
import subprocess
import sys
import concurrent.futures as futures
import traceback

import jinja2

XDG_CONFIG_DATA_DIR = os.getenv('XDG_DATA_HOME') or \
                      os.path.expanduser(os.path.join("~", ".local", "share"))
USER_DATA_DIR       = os.path.join(XDG_CONFIG_DATA_DIR, "rapport")

from rapport.config import USER_CONFIG_DIR
import rapport.template
import rapport.util


def _get_reports_path(report=None):
    path_parts = [USER_DATA_DIR, "reports"]
    if report:
        path_parts.append(report)
    return os.path.expanduser(os.path.join(*path_parts))


def list_reports():
    """Returns a list of created reports.
    """
    return sorted(os.listdir(_get_reports_path()))


def get_report(report=None):
    """Returns details of a specific report
    """
    if not report:
        report = list_reports()[-1:][0]
    report_path = _get_reports_path(report)
    report_dict = {"report": report}
    for filename in os.listdir(report_path):
        with open(os.path.join(report_path, filename), "r") as f:
            report_dict[filename] = f.read()
    return report_dict


def edit_report(report=None, type="email", email_part="body"):
    if not report:
        report = list_reports()[-1:][0]
    report_path = _get_reports_path(report)
    editor = os.getenv("EDITOR", "vi")
    if type == "email":
        report_file = "{0}.{1}.text".format(type, email_part)
    elif type == "html":
        report_file = "index.html"
    subprocess.call([editor, os.path.join(report_path, report_file)])


def create_report(plugins, timeframe):
    report_date_string = timeframe.end.strftime(rapport.util.ISO8610_FORMAT)
    report_path = _get_reports_path(report_date_string)
    if not os.path.exists(report_path):
        os.makedirs(report_path)

    # Execute all plugins in parallel and join on results:
    results = {}
    with futures.ThreadPoolExecutor(max_workers=4) as executor:
        plugin_futures = dict((executor.submit(p.try_collect, timeframe), p) for p in plugins)
        for future in futures.as_completed(plugin_futures):
            plugin = plugin_futures[future]
            try:
                res = future.result()
                if rapport.config.get_int("rapport", "verbosity") >= 2:
                    visible_result = repr(res)
                    if len(visible_result) > 1000:
                        visible_result = visible_result[:1000] + ' ...'
                    print("Result for %s: %s" % (plugin.alias, visible_result))
                tmpl = rapport.template.get_template(plugin, "text")
                if tmpl:
                    results[plugin] = tmpl.render(res)
            except jinja2.TemplateSyntaxError as e:
                print >>sys.stderr, "Syntax error in plugin {0} at {1} line {2}: {3}".format(plugin, e.name, e.lineno, e.message)
            except Exception as e:
                exc_type, exc_val, exc_tb = sys.exc_info()
                if hasattr(e, 'original_traceback'):
                    print("Traceback from plugin thread:", file=sys.stderr)
                    traceback.print_tb(e.original_traceback, file=sys.stderr)
                    print("\nTraceback from parent process:", file=sys.stderr)
                traceback.print_tb(exc_tb, file=sys.stderr)
                print("Failed plugin {0}:{1}: {2}: {3}" \
                      .format(plugin, plugin.alias, e.__class__.__name__, e),
                      file=sys.stderr)
                sys.exit(1)

    results_dict = {"login": rapport.config.get("user", "login"),
                    "date": report_date_string,
                    "plugins": plugins,
                    "results": results}

    # Render mail body template:
    template_email_body = rapport.template.get_template("body", type="email")
    email_body = template_email_body.render(results_dict)
    email_body_file = os.path.join(report_path, "email.body.text")
    with codecs.open(email_body_file, "w", encoding="utf-8") as report:
        report.write(email_body)

    # We can re-use the e-mail body as the general report body:
    results_dict["body"] = email_body

    # Render mail subject template:
    template_email_subject = rapport.template.get_template("subject", type="email")
    email_subject = template_email_subject.render(results_dict)
    email_subject_file = os.path.join(report_path, "email.subject.text")
    with open(email_subject_file, "w") as report:
        report.write(email_subject)

    #TODO: Maybe even create a Result class and return that instead of a dict?
    return results_dict


def delete_report(report):
    """Delete report(s), supports globbing.
    """
    for path in glob.glob(os.path.join(_get_reports_path(), report)):
        shutil.rmtree(path)
