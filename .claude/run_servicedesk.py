import os
import runpy

app_dir = r"C:\Users\Rhea\Desktop\Tasks\Agents framework\password-ServiceDesk\servicedesk-agent"
os.chdir(app_dir)
runpy.run_path(os.path.join(app_dir, "app.py"), run_name="__main__")
