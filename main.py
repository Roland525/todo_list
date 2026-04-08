import db

def auth_menu():
    while True:
        print("""
=== AUTH ===
1. Register
2. Login
0. Exit
""")
        choice = input("Choose: ").strip()

        if choice == "1":
            username = input("New username: ").strip()
            password = input("New password: ")
            ok = db.register_user(username, password)
            if ok:
                print("OK: user created. Now login.")
            else: 
                print("ERROR: user exists or invalid data.")

        elif choice == "2":
            username = input("Login: ").strip()
            password = input("Password: ")
            user = db.login_user(username, password)
            if user:
                print(f"OK: logged in as {user['username']}")
                return user
            else:
                print("ERROR: invalid username/password.")

        elif choice == "0":
            return None


def todo_menu(user):
    user_id = user["id"]

    while True:
        print(f"""
=== TODO (user: {user['username']}) ===
1. Add task
2. Show all tasks
3. Show not completed tasks
4. Change task status
5. Delete task
9. Logout
0. Exit
""")
        choose = input("Choose: ").strip()

        if choose == "1":
            text = input("Task: ")
            db.add_task(user_id, text)

        elif choose == "2":
            tasks = db.show_all(user_id)
            if not tasks:
                print("No tasks.")
            else:
                for t in tasks:
                    print(t)

        elif choose == "3":
            tasks = db.show_not_done(user_id)
            if not tasks:
                print("No not completed tasks.")
            else:
                for t in tasks:
                    print(t)

        elif choose == "4":
            try:
                task_id = int(input("Task id: "))
                done_in = input("Done? (1/0): ").strip()
                done = (done_in == "1")
            except ValueError:
                print("Invalid input.")
                continue

            ok = db.change_status(user_id, task_id, done)
            if not ok:
                print("ERROR: task not found (or not yours).")

        elif choose == "5":
            try:
                task_id = int(input("Task id: "))
            except ValueError:
                print("Invalid input.")
                continue

            ok = db.delete_task(user_id, task_id)
            if not ok:
                print("ERROR: task not found (or not yours).")

        elif choose == "9":
            print("Logged out.")
            return "logout"

        elif choose == "0":
            return "exit"


def main():
    try:
        db.init_db()
    except Exception as e:
        print("\n[DB ERROR]", e)
        return

    while True:
        user = auth_menu()
        if not user:
            break

        result = todo_menu(user)
        if result == "exit":
            break


if __name__ == "__main__":
    main()
