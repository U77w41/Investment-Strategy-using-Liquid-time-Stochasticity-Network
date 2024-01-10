import os

# Directories needed
dirs = [
    os.path.join("data", "raw"),
    os.path.join("data", "processed"),
    "docs",
    "config",
    "references",
    "reports",
    "notebooks",
    "saved_models",
    "src"
]

# Creating the dierectories
for dir in dirs:
    os.makedirs(dir, exist_ok=True)

files = [
    '.gitignore',  # Here we push all the files which we don't want to push into git
    'requirements.txt',
    os.path.join('src', '__init__.py'),
    os.path.join('config', 'params.yaml'),
    os.path.join('config', 'config.yaml')
]

for file_ in files:
    with open(file_, "w") as f:
        pass

# Adding a .gitkeep file inside each non-empty directory
for dir in dirs:
    if not os.listdir(dir):  # Check if the directory is empty
        with open(os.path.join(dir, ".gitkeep"), "w") as f:
            pass


