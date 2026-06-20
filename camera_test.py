import cv2

for i in range(10):

    print(f"Testing Camera {i}")

    cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)

    if cap.isOpened():

        ret, frame = cap.read()

        if ret:

            cv2.imshow(f"Camera {i}", frame)

            print(f"Camera {i} Working")

            cv2.waitKey(3000)

            cv2.destroyAllWindows()

    cap.release()